import { useEffect, useState } from "react";
import type { Json } from "../types";
import { policyConfig, units, type Domain } from "../domains";
export function PolicyEditor({
  domain,
  pack,
  busy,
  publish,
  change,
  halt,
  halted,
}: {
  domain: Domain;
  pack: Json;
  busy: boolean;
  publish: (p: Json) => void;
  change: (c: string) => void;
  halt: () => void;
  halted: boolean;
}) {
  const [policy, setPolicy] = useState<Json>(policyConfig(pack));
  useEffect(
    () => setPolicy({ ...policyConfig(pack), version: pack.version + 1 }),
    [pack.sig],
  );
  const set = (k: string, v: Json) => setPolicy({ ...policy, [k]: v });
  return (
    <details className="institution">
      <summary>
        Institution controls <span>{pack.pack_id} · signed live pack</span>
      </summary>
      <div className="institution-body">
        <p>
          These controls change this experiment’s authoritative state. Actor
          credentials cannot use them.
        </p>
        <div className="policy-fields">
          {domain === "payments" ? (
            <>
              <label>
                Session limit (USD)
                <input
                  type="number"
                  value={policy.session_limit_cents / 100}
                  onChange={(e) =>
                    set(
                      "session_limit_cents",
                      Math.round(+e.target.value * 100),
                    )
                  }
                />
              </label>
              <label>
                Per-action limit (USD)
                <input
                  type="number"
                  value={policy.per_action_cents / 100}
                  onChange={(e) =>
                    set("per_action_cents", Math.round(+e.target.value * 100))
                  }
                />
              </label>
              <label>
                Human approval above (USD)
                <input
                  type="number"
                  value={policy.approval_above_cents / 100}
                  onChange={(e) =>
                    set(
                      "approval_above_cents",
                      Math.round(+e.target.value * 100),
                    )
                  }
                />
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={policy.allow_attenuation}
                  onChange={(e) => set("allow_attenuation", e.target.checked)}
                />
                Allow amount-down attenuation
              </label>
            </>
          ) : (
            Object.keys(policy.limits || {}).map((key) => (
              <div className="policy-dimension" key={key}>
                <strong>{units[key]?.label || key}</strong>
                <label>
                  Session ceiling ({key})
                  <input
                    type="number"
                    value={policy.limits[key]}
                    onChange={(e) =>
                      set("limits", {
                        ...policy.limits,
                        [key]: +e.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Per action ({key})
                  <input
                    type="number"
                    value={policy.per_action[key]}
                    onChange={(e) =>
                      set("per_action", {
                        ...policy.per_action,
                        [key]: +e.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Human approval above ({key})
                  <input
                    type="number"
                    value={policy.approval_above[key] ?? policy.limits[key]}
                    onChange={(e) =>
                      set("approval_above", {
                        ...policy.approval_above,
                        [key]: +e.target.value,
                      })
                    }
                  />
                </label>
              </div>
            ))
          )}
          <label>
            Pack version
            <input
              type="number"
              min={pack.version + 1}
              value={policy.version}
              onChange={(e) => set("version", +e.target.value)}
            />
          </label>
          <label>
            Maximum expiry (seconds)
            <input
              type="number"
              value={policy.max_ttl_seconds}
              onChange={(e) => set("max_ttl_seconds", +e.target.value)}
            />
          </label>
        </div>
        <div className="button-row">
          <button disabled={busy} onClick={() => publish(policy)}>
            Publish signed pack
          </button>
          <button disabled={busy} onClick={() => change("resource")}>
            Advance resource version
          </button>
          {domain === "trading" && (
            <button disabled={busy} onClick={() => change("route")}>
              Advance route version
            </button>
          )}
          {domain === "referrals" && (
            <>
              <button disabled={busy} onClick={() => change("consent")}>
                Toggle consent / advance version
              </button>
              <button disabled={busy} onClick={() => change("document")}>
                Mutate synthetic document
              </button>
            </>
          )}
          <button disabled={busy} onClick={halt}>
            {halted ? "Independently restore grant" : "Halt + degrade grant"}
          </button>
        </div>
        <p className="small muted">
          Existing exposure stays held. Superseded κ fails at redemption;
          accepted earlier execution evidence remains reconcilable.
        </p>
      </div>
    </details>
  );
}
