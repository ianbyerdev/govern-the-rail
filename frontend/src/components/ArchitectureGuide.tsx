import type { Json } from "../types";
import { currency } from "../domains";

export function IntegrationChoice({
  mode,
  setMode,
  busy,
}: {
  mode: string;
  setMode: (v: string) => void;
  busy: boolean;
}) {
  return (
    <section
      className="integration-guide"
      aria-label="Integration architecture"
    >
      <div>
        <strong>The actor proposes. The RISC Runtime authorizes. The RISC Gateway enforces.</strong>
        <p>
          Adapters may live in the harness. Authority lives outside the agent
          trust boundary.
        </p>
        <div className="integration-path">
          <span>
            {mode === "native"
              ? "Application → SDK"
              : mode === "harness"
                ? "Agent → tool proposal → harness adapter"
                : "Agent → MCP-style tool → SAAC adapter"}
          </span>
          <b>→</b>
          <span>RISC Runtime · institutional authority book → κ</span>
          <b>→</b>
          <span>RISC Gateway · protected rail → ρ → Runtime reconciliation</span>
        </div>
      </div>
      <label>
        Integration mode
        <select
          value={mode}
          disabled={busy}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="native">A · Native application</option>
          <option value="harness">B · Agent harness</option>
          <option value="mcp">C · MCP / plugin-style</option>
        </select>
        <small>
          Same authority, limits and executor. Harness and MCP modes are local
          simulations, not verified vendor integrations.
        </small>
      </label>
    </section>
  );
}

export function ArchitectureScenarios({
  busy,
  demo,
  run,
  inspect,
}: {
  busy: boolean;
  demo: Json;
  run: (s: string) => void;
  inspect: (id: string) => void;
}) {
  return (
    <section
      className="architecture-scenarios"
      aria-label="Architecture demonstrations"
    >
      <div className="eyebrow">One-click payment reference scenarios</div>
      <p>
        Each opens a fresh experiment using the selected integration. Earlier
        evidence is retained.
      </p>
      <div className="button-row">
        {[
          ["normal", "Normal authorized action"],
          ["rejection", "Policy rejection"],
          ["bypass", "Direct rail bypass"],
          ["replay", "Replay attack"],
          ["retry", "Authorization retry"],
          ["mutation", "Capability mutation"],
          ["expiry", "Expired capability"],
          ["delegation", "Delegation violation"],
        ].map(([id, label]) => (
          <button key={id} disabled={busy} onClick={() => run(id)}>
            {label}
          </button>
        ))}
      </div>
      {demo && (
        <div className="demonstration-result" aria-live="polite">
          <strong>
            {demo.scenario === "bypass"
              ? "Same rail, same action. Authority makes the difference."
              : demo.note}
          </strong>
          {demo.lineage.length > 0 && (
            <div className="delegation-tree" aria-label="Delegated authority">
              {demo.lineage.map((node: Json) => (
                <div key={node.grant.id}>
                  <span>{node.label}</span>
                  <strong>
                    {node.grant.operations.length
                      ? `≤ ${currency(node.grant.max_amount_cents)}`
                      : "Read-only · no mutation grant"}
                  </strong>
                  <small>
                    {node.label === "Child D"
                      ? "Acme only"
                      : node.grant.operations.join(", ")}
                  </small>
                  <code>{node.grant.id}</code>
                </div>
              ))}
            </div>
          )}
          <ol className="demonstration-attempts">
            {demo.attempts.map((attempt: Json, i: number) => (
              <li key={i}>
                <span className={attempt.accepted ? "success" : "refusal"}>
                  {attempt.status_label || (attempt.accepted ? "EXECUTED" : "REJECTED")}
                </span>
                <div>
                  <strong>{attempt.label}</strong>
                  <small>{attempt.code}</small>
                </div>
                {attempt.run_id && (
                  <button
                    disabled={busy}
                    onClick={() => inspect(attempt.run_id)}
                  >
                    Inspect action {i + 1}
                  </button>
                )}
              </li>
            ))}
          </ol>
          {demo.lineage.length > 0 && (
            <p className="small muted">
              A child may narrow amounts, operations, destinations and lifetime.
              Each child shares its parent’s book.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
