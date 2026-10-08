"""Runs the graph: polls the inbox, processes queued messages, resumes paused runs after review.

Reliability:
* node-level RetryPolicy (in graph.py) absorbs transient LLM/tool errors;
* if a run still fails, the message is retried later with exponential backoff (1 min, 4 min),
  resuming from its last checkpoint; after 3 failed runs it goes to the dead-letter queue;
* the SQLite checkpointer keeps paused (awaiting review) and half-finished runs across restarts.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from inboxpilot import erp, store
from inboxpilot.config import get_settings
from inboxpilot.graph import build_graph
from inboxpilot.llm import tracing_callbacks
from inboxpilot.sources import mock

log = logging.getLogger("inboxpilot")
MAX_RUNS = 3


class Runtime:
    def __init__(self) -> None:
        self.graph = None
        self._stack = AsyncExitStack()
        self._task: asyncio.Task | None = None
        self._sem = asyncio.Semaphore(2)
        self._inflight: set[str] = set()

    async def start(self, background: bool = True) -> None:
        s = get_settings()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        s.inbox_dir.mkdir(parents=True, exist_ok=True)
        store.init()
        erp.init_erp()
        saver = await self._stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(s.checkpoint_path)))
        self.graph = build_graph().compile(checkpointer=saver)
        resumed = store.requeue_interrupted()
        if resumed:
            log.info("Requeued %d interrupted runs; they resume from their last checkpoint", resumed)
        if background:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._stack.aclose()

    def config(self, message_id: str) -> dict:
        return {"configurable": {"thread_id": message_id}, "callbacks": tracing_callbacks(),
                "metadata": {"message_id": message_id}, "run_name": f"inbox:{message_id}"}

    # ---------------------------------------------------------------- loop
    async def _loop(self) -> None:
        s = get_settings()
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll failed")
            await asyncio.sleep(s.poll_interval_seconds)

    async def poll_once(self) -> None:
        s = get_settings()
        if s.inbox_mode == "gmail":
            from inboxpilot.sources import gmail

            await asyncio.to_thread(gmail.poll)
        await asyncio.to_thread(mock.poll)  # the folder works in every mode (demo + uploads)
        for m in store.due_messages():
            if m["id"] not in self._inflight:
                self._inflight.add(m["id"])
                asyncio.create_task(self._guarded(m))

    async def _guarded(self, m: dict) -> None:
        try:
            async with self._sem:
                await self.process(m)
        finally:
            self._inflight.discard(m["id"])

    # ---------------------------------------------------------------- runs
    async def process(self, m: dict) -> str:
        cfg = self.config(m["id"])
        store.update(m["id"], status="processing", attempts=m["attempts"] + 1)
        snapshot = await self.graph.aget_state(cfg)
        payload = None if snapshot.next else {"message": _message_state(m)}  # resume from checkpoint if a run exists
        try:
            await self.graph.ainvoke(payload, cfg)
        except Exception as exc:
            return self._fail(m, exc)
        return await self._settle(m["id"])

    async def review(self, message_id: str, decision: dict) -> str:
        cfg = self.config(message_id)
        snapshot = await self.graph.aget_state(cfg)
        if not snapshot.interrupts:
            raise ValueError("This message is not waiting for review")
        store.update(message_id, status="processing")
        try:
            await self.graph.ainvoke(Command(resume=decision), cfg)
        except Exception as exc:
            store.update(message_id, status="awaiting_review", last_error=f"{type(exc).__name__}: {exc}"[:500])
            raise
        return await self._settle(message_id)

    async def _settle(self, message_id: str) -> str:
        snapshot = await self.graph.aget_state(self.config(message_id))
        if snapshot.interrupts:
            status = "awaiting_review"
            payload = snapshot.interrupts[0].value
            store.add_event(message_id, "human_review", "waiting", None,
                            {"reason": payload.get("reason") or "Approval required before sending"})
        else:
            status = snapshot.values.get("outcome", "archived")
        store.update(message_id, status=status, last_error=None, next_attempt_at=None)
        return status

    def _fail(self, m: dict, exc: Exception) -> str:
        attempts = m["attempts"] + 1
        error = f"{type(exc).__name__}: {exc}"[:500]
        log.warning("run failed for %s (attempt %d): %s", m["id"], attempts, error)
        if attempts >= MAX_RUNS:
            store.update(m["id"], status="dead_letter", last_error=error)
            store.add_event(m["id"], "dead_letter", "error", None, {"error": error, "runs": attempts})
            return "dead_letter"
        delay = timedelta(minutes=4 ** (attempts - 1))
        store.update(m["id"], status="queued", last_error=error,
                     next_attempt_at=(datetime.now(timezone.utc) + delay).isoformat(timespec="seconds"))
        return "queued"

    async def state(self, message_id: str) -> dict:
        snapshot = await self.graph.aget_state(self.config(message_id))
        values = dict(snapshot.values or {})
        values.pop("message", None)
        values["waiting_for_review"] = bool(snapshot.interrupts)
        return values


def _message_state(m: dict) -> dict:
    keys = ("id", "source", "external_id", "thread_id", "sender", "sender_name", "subject", "body", "received_at")
    return {k: m[k] for k in keys}


runtime = Runtime()
