"use client";

import { useState } from "react";

import type { Evidence } from "@/types/resumeProfile";

interface EvidencePanelProps {
  evidence: Evidence[];
  loadSource: () => Promise<string>;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ");
}

/** Locate `quote` in `source`, tolerant of whitespace differences. */
function findQuote(source: string, quote: string): { start: number; end: number } | null {
  const target = normalize((quote.split(/\.{3,}|…/)[0] ?? "").trim());
  if (!target) return null;
  // Map normalized offsets back to the original text.
  const map: number[] = [];
  let normalized = "";
  let previousSpace = false;
  for (let i = 0; i < source.length; i++) {
    const ch = source.charAt(i);
    const isSpace = /\s/.test(ch);
    if (isSpace && previousSpace) continue;
    normalized += isSpace ? " " : ch.toLowerCase();
    map.push(i);
    previousSpace = isSpace;
  }
  const index = normalized.indexOf(target);
  const start = map[index];
  const last = map[index + target.length - 1];
  if (index === -1 || start === undefined || last === undefined) return null;
  return { start, end: last + 1 };
}

const CONTEXT_CHARS = 160;

function SourceContext({ source, quote }: { source: string; quote: string }) {
  const match = findQuote(source, quote);
  if (!match) {
    return (
      <p className="text-xs text-amber-700">
        This quote could not be located in the extracted resume text.
      </p>
    );
  }
  const from = Math.max(0, match.start - CONTEXT_CHARS);
  const to = Math.min(source.length, match.end + CONTEXT_CHARS);
  return (
    <pre
      className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-xs text-slate-600"
      data-testid="source-context"
    >
      {from > 0 && "…"}
      {source.slice(from, match.start)}
      <mark className="rounded bg-yellow-200 px-0.5 text-slate-900">
        {source.slice(match.start, match.end)}
      </mark>
      {source.slice(match.end, to)}
      {to < source.length && "…"}
    </pre>
  );
}

export function EvidencePanel({ evidence, loadSource }: EvidencePanelProps) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);

  if (evidence.length === 0) {
    return <p className="text-xs text-slate-500">No source quote was recorded for this item.</p>;
  }

  async function toggleSource(index: number) {
    if (openIndex === index) {
      setOpenIndex(null);
      return;
    }
    setOpenIndex(index);
    if (source === null) {
      try {
        setSource(await loadSource());
      } catch {
        setSourceError("Unable to load the resume text.");
      }
    }
  }

  return (
    <ul className="flex flex-col gap-2" aria-label="Evidence">
      {evidence.map((item, index) => (
        <li key={index} className="rounded-md border border-slate-100 bg-white p-2">
          <blockquote className="text-sm italic text-slate-700">“{item.source_text}”</blockquote>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            {item.source_section && <span>{item.source_section}</span>}
            {item.verified ? (
              <span className="text-emerald-700">✓ Found in your resume</span>
            ) : (
              <span className="text-amber-700">⚠ Not found word-for-word in your resume</span>
            )}
            <button
              type="button"
              className="text-brand-700 underline"
              onClick={() => void toggleSource(index)}
              aria-expanded={openIndex === index}
            >
              {openIndex === index ? "Hide source" : "View source"}
            </button>
          </div>
          {openIndex === index && (
            <div className="mt-2">
              {sourceError ? (
                <p className="text-xs text-red-700">{sourceError}</p>
              ) : source === null ? (
                <p className="text-xs text-slate-500">Loading...</p>
              ) : (
                <SourceContext source={source} quote={item.source_text} />
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
