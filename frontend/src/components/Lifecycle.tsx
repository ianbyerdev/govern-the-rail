import { useState } from "react";
import type { Json, Run } from "../types";

const stages = [
  ["PROPOSED", "PROPOSAL_CREATED", "actor"],
  ["RESOLVED", "EFFECT_RESOLVED", "authority"],
  ["CHECKED", "POLICY_EVALUATED", "authority"],
  ["RESERVED", "RISK_RESERVED", "authority"],
  ["κ ISSUED", "CAPABILITY_ISSUED", "authority"],
  ["EXECUTOR VERIFIED", "CAPABILITY_VERIFIED", "rail"],
  ["EXECUTED", "EXECUTION_COMPLETED", "rail"],
  ["RECEIPT ρ", "RECEIPT_CREATED", "rail"],
  ["RECONCILED", "RECEIPT_RECONCILED", "authority"],
];

export function Lifecycle({ run, view, busy }: {
  run: Run | null;
  view: Json;
  busy: boolean;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const events = run?.events || [];
  const event = events.filter((e) => e.kind === selected).at(-1);
  const reservation = view.reservations.find(
    (r: Json) => r.id === run?.reservation_id,
  );
  const grant = view.grants.find((g: Json) => g.id === run?.proposal.grant_id);
  return (
    <section className="lifecycle" aria-label="SAAC lifecycle" aria-busy={busy}>
      <div className="lifecycle-stages">
        {stages.map(([label, kind, owner]) => {
          const recorded = events.some((e) => e.kind === kind);
          return (
            <button
              key={kind}
              disabled={busy || !recorded}
              aria-pressed={selected === kind}
              className={`${owner} ${recorded ? "recorded" : ""}`}
              onClick={() => setSelected(selected === kind ? null : kind)}
            >
              <span>{recorded ? "✓" : "○"}</span>
              {label}
            </button>
          );
        })}
      </div>
      <p className="small muted">
        Recorded stages only. Select a stage to inspect its evidence. Denial
        stops the path; queued dispatch is not a completed job.
      </p>
      {event && (
        <div className="lifecycle-inspector">
          <div className="button-row">
            <strong>{event.kind.replaceAll("_", " ")}</strong>
            <button onClick={() => setSelected(null)}>
              Close stage details
            </button>
          </div>
          <dl className="lifecycle-context">
            <dt>Principal / agent</dt>
            <dd>
              {run?.proposal.principal_id} / {run?.proposal.agent_id}
            </dd>
            <dt>Authority lineage</dt>
            <dd>
              {[...(grant?.chain || []), grant?.id].filter(Boolean).join(" → ")}
            </dd>
            <dt>Policy / pack</dt>
            <dd>{run?.snapshot?.pack_id || "Unresolved"}</dd>
            <dt>Executor</dt>
            <dd>{run?.socket_decision?.code || "Not presented"}</dd>
          </dl>
          <pre>
            {JSON.stringify(
              {
                event,
                state_version: run?.snapshot?.effect.c,
                reservation,
                kappa: run?.kappa,
                executor_decision: run?.socket_decision,
                receipt:
                  run?.receipt ||
                  events.filter((e) => e.kind === "RECEIPT_CREATED").at(-1)
                    ?.data,
                final_authoritative_risk: view.risk,
                final_authoritative_state: view.state,
              },
              null,
              2,
            )}
          </pre>
          <p className="small muted">
            Event data is historical. Reservation and final risk are the current
            selected book state.
          </p>
        </div>
      )}
    </section>
  );
}
