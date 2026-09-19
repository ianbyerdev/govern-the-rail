import { useState } from "react";
import { Download, ShieldCheck } from "lucide-react";
import type { Json, Run, Event } from "../types";
const labels: Record<string, string> = {
  proposal: "Raw proposal",
  snapshot: "Resolved snapshot",
  decision: "Verdict",
  kappa: "κ Capability",
  receipt: "ρ Receipt",
  timeline: "Event timeline",
};
export function EvidencePanel({
  run,
  onTape,
  busy,
}: {
  run: Run | null;
  onTape: () => Promise<Json>;
  busy: boolean;
}) {
  const [tab, setTab] = useState("kappa"),
    [event, setEvent] = useState<Event | null>(null),
    [report, setReport] = useState<Json>(null);
  const receipt =
    run?.receipt ||
    run?.events?.filter((e) => e.kind === "RECEIPT_CREATED").at(-1)?.data;
  const data =
    tab === "timeline"
      ? event?.data
      : tab === "receipt"
        ? receipt
        : (run as Json)?.[tab];
  function download(value: Json, name: string) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <details className="evidence">
      <summary>
        Inspect bindings, signed κ / ρ, and event timeline{" "}
        <span>{run?.events?.length || 0} events</span>
      </summary>
      <div className="evidence-body">
        <div className="evidence-toolbar">
          <nav aria-label="Evidence object">
            {Object.entries(labels).map(([id, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={tab === id}
                key={id}
                onClick={() => {
                  setTab(id);
                  setEvent(null);
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          <button
            disabled={!run}
            onClick={() => download(run, `${run?.id}.json`)}
          >
            <Download size={15} />
            Export action
          </button>
        </div>
        {tab === "receipt" && !run?.receipt && receipt && (
          <p className="callout warning">
            This receipt exists at the socket. The risk book has not accepted it
            yet.
          </p>
        )}
        <div className={tab === "timeline" ? "timeline-layout" : ""}>
          {tab === "timeline" && (
            <ol className="event-list">
              {run?.events?.map((e) => (
                <li key={e.seq}>
                  <button
                    className={e.seq === event?.seq ? "selected" : ""}
                    onClick={() => setEvent(e)}
                  >
                    <span>{e.seq}</span>
                    <strong>{e.kind.replaceAll("_", " ")}</strong>
                  </button>
                </li>
              ))}
            </ol>
          )}
          <pre className="json-view">
            {data
              ? JSON.stringify(data, null, 2)
              : "Select an event or run an action to inspect its recorded evidence."}
          </pre>
        </div>
        <div className="tape-tools">
          <button
            disabled={busy}
            onClick={async () => {
              const tape = await onTape();
              setReport(tape);
            }}
          >
            <ShieldCheck size={16} />
            Verify complete tape
          </button>
          {report && (
            <>
              <span className="small">
                {report.verification.valid ? "Verified" : "Invalid"} ·{" "}
                {report.verification.capabilities_verified} κ ·{" "}
                {report.verification.receipts_verified} reconciled ρ
              </span>
              <button onClick={() => download(report, "saac-tape.json")}>
                Export tape
              </button>
            </>
          )}
        </div>
        <p className="small muted">
          Historical evidence does not undo execution. The book in the risk lane
          is current. The local hash chain has no independent external
          checkpoint.
        </p>
      </div>
    </details>
  );
}
