"use client";

import { useEffect, useRef } from "react";

export function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const mermaid = (await import("mermaid")).default;
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
      const { svg } = await mermaid.render(`g${Math.random().toString(36).slice(2)}`, chart);
      if (!cancelled && ref.current) ref.current.innerHTML = svg;
    })();
    return () => {
      cancelled = true;
    };
  }, [chart]);
  return <div ref={ref} className="flex justify-center [&_svg]:max-w-full" />;
}
