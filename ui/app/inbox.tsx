"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, postJson } from "@/lib/api";
import { Mermaid } from "./mermaid";

type Status = "queued" | "processing" | "awaiting_review" | "sent" | "rejected" | "archived" | "dead_letter";
type MessageRow = {
  id: string;
  source: string;
  sender: string;
  sender_name: string | null;
  subject: string | null;
  preview: string;
  received_at: string;
  status: Status;
  intent: string | null;
  confidence: number | null;
  last_error: string | null;
};
type Message = MessageRow & { body: string; attempts: number };
type Event = { id: number; at: string; step: string; status: string; duration_ms: number | null; detail: Record<string, unknown> | null };
type Draft = { subject: string; body: string; internal_note: string };
type GraphState = {
  classification?: { intent: string; confidence: number; reason: string };
  extraction?: Record<string, unknown>;
  facts?: Record<string, unknown>;
  attachments?: string[];
  draft?: Draft;
  needs_human?: boolean;
  human_reason?: string;
  review?: { action: string; reviewer?: string };
  outcome?: string;
  delivery?: string;
  waiting_for_review: boolean;
};
type Outbox = { subject: string; body: string; attachments: string[]; edited: number; reviewer: string; sent_at: string; delivery: string };
type Detail = { message: Message; state: GraphState; events: Event[]; outbox: Outbox[] };
type Stats = { by_status: Record<Status, number>; sent_unedited: number; sent_edited: number };

const FILTERS: { label: string; status?: Status }[] = [
  { label: "Needs review", status: "awaiting_review" },
  { label: "All" },
  { label: "Sent", status: "sent" },
  { label: "Archived", status: "archived" },
  { label: "Dead letter", status: "dead_letter" },
];

const STATUS_STYLE: Record<Status, string> = {
  queued: "bg-subtle text-muted",
  processing: "bg-accent-soft text-accent",
  awaiting_review: "bg-warn-soft text-warn",
  sent: "bg-ok-soft text-ok",
  rejected: "bg-subtle text-muted",
  archived: "bg-subtle text-muted",
  dead_letter: "bg-danger-soft text-danger",
};

const DEMO = ["01-quote-request.json", "02-angry-complaint.json", "03-order-status.json"];

export function Inbox() {
  const [filter, setFilter] = useState(0);
  const [rows, setRows] = useState<MessageRow[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const upload = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const status = FILTERS[filter].status;
    try {
      const [r, s] = await Promise.all([api<MessageRow[]>(`/api/messages${status ? `?status=${status}` : ""}`), api<Stats>("/api/stats")]);
      setRows(r);
      setStats(s);
    } catch {}
  }, [filter]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const t = setInterval(refresh, 2000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [refresh]);

  async function loadDemo(names?: string[]) {
    const res = await postJson<{ copied: string[] }>("/api/demo/load", { names });
    setNotice(`Dropped ${res.copied.length} emails into the inbox. They'll appear within a few seconds.`);
    setFilter(1);
    setTimeout(() => setNotice(null), 5000);
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    await api("/api/upload", { method: "POST", body: form });
    setNotice(`Queued ${files.length} file(s).`);
    setTimeout(() => setNotice(null), 4000);
  }

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="flex w-full max-w-md flex-col border-r border-line bg-panel">
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <button onClick={() => loadDemo(DEMO)} className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white">
            Drop in 3 demo emails
          </button>
          <button onClick={() => loadDemo()} className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-subtle">
            All samples
          </button>
          <input ref={upload} type="file" accept=".eml,.json" multiple hidden onChange={(e) => uploadFiles(e.target.files)} />
          <button onClick={() => upload.current?.click()} className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-subtle">
            Upload .eml
          </button>
          <button onClick={() => setShowGraph(true)} className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-subtle">
            View graph
          </button>
        </div>
        {notice && <div className="border-b border-line bg-accent-soft px-3 py-2 text-xs text-accent">{notice}</div>}
        <div className="flex gap-1 overflow-x-auto border-b border-line px-2 py-2">
          {FILTERS.map((f, i) => {
            const n = f.status ? stats?.by_status[f.status] : undefined;
            return (
              <button
                key={f.label}
                onClick={() => setFilter(i)}
                className={`whitespace-nowrap rounded-md px-2.5 py-1 text-xs ${filter === i ? "bg-subtle font-semibold" : "text-muted hover:text-fg"}`}
              >
                {f.label}
                {n ? <span className="ml-1 rounded bg-warn-soft px-1 text-warn">{n}</span> : null}
              </button>
            );
          })}
        </div>
        <ul className="flex-1 overflow-y-auto">
          {rows.length === 0 && <li className="p-6 text-center text-sm text-muted">Nothing here. Drop in the demo emails to start.</li>}
          {rows.map((m) => (
            <li key={m.id}>
              <button
                onClick={() => setSelected(m.id)}
                className={`w-full border-b border-line px-4 py-3 text-left hover:bg-subtle ${selected === m.id ? "bg-subtle" : ""}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{m.sender_name || m.sender}</span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_STYLE[m.status]}`}>{m.status.replace("_", " ")}</span>
                </div>
                <div className="truncate text-sm">{m.subject || "(no subject)"}</div>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                  {m.source !== "email" && <span className="rounded bg-subtle px-1">{m.source}</span>}
                  {m.intent && (
                    <span>
                      {m.intent.replace("_", " ")} · {((m.confidence ?? 0) * 100).toFixed(0)}%
                    </span>
                  )}
                  <span className="truncate">{m.preview}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
        {stats && (
          <div className="border-t border-line px-4 py-2 text-[11px] text-muted">
            Sent: {stats.sent_unedited} approved as drafted · {stats.sent_edited} edited by a human
          </div>
        )}
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto">
        {selected ? <MessageDetail id={selected} onChanged={refresh} /> : <Empty />}
      </section>

      {showGraph && <GraphModal onClose={() => setShowGraph(false)} />}
    </div>
  );
}

function Empty() {
  return (
    <div className="mx-auto mt-24 max-w-md text-center text-sm text-muted">
      <p className="text-base font-medium text-fg">Select a message</p>
      <p className="mt-2">
        Each email runs through a LangGraph agent: classify, extract, check the ERP, draft. It then waits here for you to approve, edit
        or reject. Only approved replies are sent.
      </p>
    </div>
  );
}

function MessageDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadedFor = useRef<string | null>(null);

  const load = useCallback(async () => {
    const res = await api<Detail>(`/api/messages/${id}`);
    setD(res);
    // initialise the editor once per message, when the draft first becomes available
    const key = `${id}:${res.state.draft ? "draft" : "none"}`;
    if (loadedFor.current !== key) {
      loadedFor.current = key;
      setSubject(res.state.draft?.subject ?? `Re: ${res.message.subject ?? ""}`);
      setBody(res.state.draft?.body ?? "");
    }
  }, [id]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const t = setInterval(load, 2000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load]);

  async function act(action: "approve" | "edit" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await postJson(`/api/messages/${id}/review`, action === "reject" ? { action } : { action, subject, body });
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    await postJson(`/api/messages/${id}/retry`, {});
    load();
    onChanged();
  }

  if (!d) return <div className="p-6 text-sm text-muted">Loading…</div>;
  const { message: m, state: s, events, outbox } = d;
  const edited = !!s.draft && (body !== s.draft.body || subject !== s.draft.subject);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 p-6">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold">{m.subject || "(no subject)"}</h2>
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_STYLE[m.status]}`}>{m.status.replace("_", " ")}</span>
        </div>
        <p className="text-sm text-muted">
          {m.sender_name ? `${m.sender_name} <${m.sender}>` : m.sender} · {m.source} · {new Date(m.received_at).toLocaleString()}
        </p>
        <pre className="mt-3 whitespace-pre-wrap rounded-xl border border-line bg-panel p-4 font-sans text-sm">{m.body}</pre>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="flex flex-col gap-5">
          {s.classification && (
            <Card title="Classification">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded bg-accent-soft px-2 py-0.5 font-medium text-accent">{s.classification.intent.replace("_", " ")}</span>
                <ConfidenceBar value={s.classification.confidence} />
              </div>
              <p className="mt-2 text-xs text-muted">{s.classification.reason}</p>
              {s.needs_human && <p className="mt-2 rounded-md bg-warn-soft p-2 text-xs text-warn">{s.human_reason} The agent did not draft a reply; write one below.</p>}
            </Card>
          )}

          {s.extraction && (
            <Card title="Extracted fields">
              <KeyValues data={s.extraction} />
            </Card>
          )}

          {s.facts && Object.keys(s.facts).length > 0 && (
            <Card title="From the ERP (tool calls)">
              <Facts facts={s.facts} />
            </Card>
          )}

          {s.waiting_for_review && (
            <Card title={s.draft ? "Draft reply — review before sending" : "Write a reply"}>
              {s.draft?.internal_note && <p className="mb-3 rounded-md bg-subtle p-2 text-xs text-muted">Note for reviewer: {s.draft.internal_note}</p>}
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="mb-2 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
              />
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={10}
                className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm leading-relaxed outline-none focus:border-accent"
              />
              {s.attachments && s.attachments.length > 0 && <Attachments names={s.attachments} />}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {s.draft && !edited ? (
                  <button disabled={busy} onClick={() => act("approve")} className="rounded-md bg-ok px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
                    Approve &amp; send
                  </button>
                ) : (
                  <button
                    disabled={busy || !body.trim()}
                    onClick={() => act("edit")}
                    className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Send edited reply
                  </button>
                )}
                <button disabled={busy} onClick={() => act("reject")} className="rounded-md border border-line px-4 py-2 text-sm hover:bg-subtle">
                  Reject
                </button>
                {edited && s.draft && (
                  <button
                    onClick={() => {
                      setSubject(s.draft!.subject);
                      setBody(s.draft!.body);
                    }}
                    className="text-xs text-muted hover:text-fg"
                  >
                    Reset to draft
                  </button>
                )}
                {error && <span className="text-sm text-danger">{error}</span>}
              </div>
            </Card>
          )}

          {outbox.map((o, i) => (
            <Card key={i} title={`Sent · ${new Date(o.sent_at).toLocaleString()}`}>
              <p className="text-xs text-muted">
                Approved by {o.reviewer}
                {o.edited ? " (edited)" : " (as drafted)"} · {o.delivery}
              </p>
              <p className="mt-2 text-sm font-medium">{o.subject}</p>
              <pre className="mt-1 whitespace-pre-wrap font-sans text-sm">{o.body}</pre>
              {o.attachments.length > 0 && <Attachments names={o.attachments} />}
            </Card>
          ))}

          {m.status === "dead_letter" && (
            <Card title="Dead letter">
              <p className="text-sm text-danger">{m.last_error}</p>
              <p className="mt-1 text-xs text-muted">Failed {m.attempts} runs (each with node-level retries).</p>
              <button onClick={retry} className="mt-3 rounded-md border border-line px-3 py-1.5 text-sm hover:bg-subtle">
                Retry
              </button>
            </Card>
          )}
          {m.status === "queued" && m.last_error && <p className="text-xs text-warn">Last attempt failed, retrying with backoff: {m.last_error}</p>}
        </div>

        <Card title="Trace">
          <ol className="relative flex flex-col gap-3 border-l border-line pl-4">
            {events.length === 0 && <li className="text-xs text-muted">Waiting for the agent…</li>}
            {events.map((e) => (
              <li key={e.id} className="relative">
                <span
                  className={`absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ${
                    e.status === "error" ? "bg-danger" : e.status === "waiting" ? "bg-warn" : "bg-ok"
                  }`}
                />
                <div className="flex items-center justify-between text-xs">
                  <span className="font-medium">{e.step.replace("_", " ")}</span>
                  <span className="text-muted">{e.duration_ms != null ? `${e.duration_ms} ms` : e.status}</span>
                </div>
                {e.status === "error" && <p className="text-[11px] text-danger">{String(e.detail?.error ?? "")}</p>}
                {e.step === "human_review" && e.detail && (
                  <p className="text-[11px] text-muted">{String(e.detail.reason ?? e.detail.reviewer ?? "")}</p>
                )}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  return (
    <span className="flex items-center gap-2 text-xs text-muted">
      <span className="h-1.5 w-24 overflow-hidden rounded bg-subtle">
        <span className={`block h-full ${value >= 0.7 ? "bg-ok" : "bg-warn"}`} style={{ width: `${pct}%` }} />
      </span>
      {pct}% confidence
    </span>
  );
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 text-sm">
      {Object.entries(data).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
          <dd className={v == null ? "text-muted" : ""}>
            {Array.isArray(v)
              ? v.map((item, i) => <div key={i}>{typeof item === "object" ? Object.values(item as object).join(" × ") : String(item)}</div>)
              : v == null
                ? "—"
                : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Facts({ facts }: { facts: Record<string, unknown> }) {
  const lines = facts.lines as Array<Record<string, unknown>> | undefined;
  return (
    <div className="flex flex-col gap-3 text-sm">
      {lines && (
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1">Requested</th>
              <th>Matched SKU</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Total</th>
              <th className="text-right">Ships in</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-1">{String(l.requested)}</td>
                <td>{l.matched ? `${l.sku} · ${l.name}` : <span className="text-danger">not in catalogue</span>}</td>
                <td className="text-right">{l.quantity ? Number(l.quantity).toLocaleString() : "?"}</td>
                <td className="text-right">{l.line_total != null ? `$${Number(l.line_total).toLocaleString()}` : "—"}</td>
                <td className="text-right">{l.ship_in_days != null ? `${l.ship_in_days} d` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {Object.entries(facts)
        .filter(([k]) => k !== "lines")
        .map(([k, v]) => (
          <div key={k}>
            <div className="text-xs font-medium text-muted">{k}</div>
            <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap rounded-md bg-subtle p-2 font-mono text-[11px]">
              {typeof v === "string" ? v : JSON.stringify(v, null, 2)}
            </pre>
          </div>
        ))}
    </div>
  );
}

function Attachments({ names }: { names: string[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {names.map((n) => (
        <a
          key={n}
          href={`/api/backend/api/quotes/${encodeURIComponent(n)}`}
          target="_blank"
          rel="noreferrer"
          className="rounded-md border border-line bg-subtle px-2 py-1 text-xs hover:border-accent"
        >
          📎 {n}
        </a>
      ))}
    </div>
  );
}

function GraphModal({ onClose }: { onClose: () => void }) {
  const [chart, setChart] = useState<string | null>(null);
  useEffect(() => {
    api<{ mermaid: string }>("/api/graph").then((r) => setChart(r.mermaid));
  }, []);
  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div className="max-h-full w-full max-w-xl overflow-auto rounded-2xl bg-panel p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold">LangGraph state graph</h2>
          <button onClick={onClose} className="text-sm text-muted hover:text-fg">
            Close
          </button>
        </div>
        {chart ? <Mermaid chart={chart} /> : <p className="text-sm text-muted">Loading…</p>}
      </div>
    </div>
  );
}
