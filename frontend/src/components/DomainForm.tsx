import type { Domain } from "../domains";
import type { Json } from "../types";
export function DomainForm({
  domain,
  draft,
  setDraft,
  busy,
  issued,
}: {
  domain: Domain;
  draft: Json;
  setDraft: (p: Json) => void;
  busy: boolean;
  issued: boolean;
}) {
  const set = (key: string, value: Json) =>
    setDraft({ ...draft, [key]: value });
  return (
    <div className="domain-form">
      <div className="eyebrow">
        {issued ? "Draft for next proposal" : "Raw proposal"}
      </div>
      {domain === "payments" && (
        <>
          <div className="field-row">
            <label>
              Amount (USD)
              <input
                aria-label="Amount (USD)"
                type="number"
                min="0.01"
                step="0.01"
                value={draft.amount_cents / 100}
                onChange={(e) =>
                  set("amount_cents", Math.round(Number(e.target.value) * 100))
                }
              />
            </label>
            <label>
              Currency
              <select
                value={draft.currency}
                onChange={(e) => set("currency", e.target.value)}
              >
                <option>USD</option>
                <option>EUR</option>
              </select>
            </label>
          </div>
          <label>
            Beneficiary
            <select
              value={draft.beneficiary}
              onChange={(e) => set("beneficiary", e.target.value)}
            >
              <option value="acme">Acme Research</option>
              <option value="community">Community Lab</option>
              <option value="payroll">Payroll alias → Acme</option>
              <option value="unknown">Unlisted recipient</option>
            </select>
          </label>
          <label>
            Route
            <select
              value={draft.route}
              onChange={(e) => set("route", e.target.value)}
            >
              <option>local-ach</option>
              <option>local-wire</option>
            </select>
          </label>
          <label>
            Purpose
            <select
              value={draft.purpose}
              onChange={(e) => set("purpose", e.target.value)}
            >
              <option>invoice</option>
              <option>research</option>
            </select>
          </label>
        </>
      )}
      {domain === "trading" && (
        <>
          <div className="field-row">
            <label>
              Shares
              <input
                type="number"
                min="1"
                value={draft.quantity}
                onChange={(e) => set("quantity", Number(e.target.value))}
              />
            </label>
            <label>
              Limit (USD)
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={draft.limit_price_cents / 100}
                onChange={(e) =>
                  set(
                    "limit_price_cents",
                    Math.round(Number(e.target.value) * 100),
                  )
                }
              />
            </label>
          </div>
          <label>
            Instrument
            <select
              value={draft.instrument}
              onChange={(e) => set("instrument", e.target.value)}
            >
              <option>XYZ</option>
              <option>XYZ.US</option>
            </select>
          </label>
          <label>
            Execution route
            <select
              value={draft.route}
              onChange={(e) => set("route", e.target.value)}
            >
              <option>BROKER-A/session17</option>
              <option>BROKER-B/session19</option>
            </select>
          </label>
          <p className="small muted">
            Buy limit · CASH-1 · DAY
            <br />
            Synthetic OMS → exclusively owned local EMS
          </p>
        </>
      )}
      {domain === "referrals" && (
        <>
          <label>
            Patient
            <select
              value={draft.patient}
              onChange={(e) => set("patient", e.target.value)}
            >
              <option>P-104</option>
              <option>P-205</option>
            </select>
          </label>
          <label>
            Recipient
            <select
              value={draft.recipient}
              onChange={(e) => set("recipient", e.target.value)}
            >
              <option value="north-clinic">North Clinic · synthetic</option>
              <option value="south-clinic">South Clinic · synthetic</option>
            </select>
          </label>
          <label>
            Records in packet
            <input
              type="number"
              min="1"
              max="7"
              value={draft.documents.length}
              onChange={(e) =>
                set(
                  "documents",
                  Array.from(
                    {
                      length: Math.max(1, Math.min(7, Number(e.target.value))),
                    },
                    (_, i) => `doc-${i + 1}`,
                  ),
                )
              }
            />
          </label>
          <p className="small muted">
            Purpose: referral. The institution resolves and seals the actual
            record bytes.
          </p>
        </>
      )}
      {domain === "runtime" && (
        <>
          <div className="fixed-job">
            <strong>audit.py</strong>
            <p>Fixed, inspectable probe script</p>
            <code>/workspace/report.txt</code>
          </div>
          <p className="small muted">
            Workspace: demo
            <br />
            No external network · no extra arguments
            <br />
            Read-only runtime · no institution keys
          </p>
        </>
      )}
      <label>
        Capability lifetime (seconds)
        <input
          type="number"
          min="1"
          max="3600"
          value={draft.ttl_seconds}
          onChange={(e) => set("ttl_seconds", Number(e.target.value))}
          disabled={busy}
        />
      </label>
      <div className="actor-belief">
        <div className="eyebrow">Actor belief · non-authoritative</div>
        <label>
          Claimed available capacity
          <input
            aria-label="Actor claimed available capacity"
            type="number"
            value={draft.actor_risk_observed?.available ?? 999999}
            onChange={(e) =>
              set("actor_risk_observed", { available: Number(e.target.value) })
            }
          />
        </label>
        <p>The risk book ignores this number.</p>
      </div>
      {issued && (
        <p className="small muted">
          Edits belong to the next proposal. The reviewed action remains fixed
          in the risk lane.
        </p>
      )}
    </div>
  );
}
