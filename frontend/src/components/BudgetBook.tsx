import { units, domains, type Domain } from "../domains";
import type { Json } from "../types";
export function BudgetBook({
  metrics,
  domain,
}: {
  metrics: Json;
  domain: Domain;
}) {
  const primary = domains[domain].unit;
  const keys = Object.keys(metrics).sort((a, b) =>
    a === primary ? -1 : b === primary ? 1 : a.localeCompare(b),
  );
  return (
    <div className="budget-book" aria-label="Authoritative risk book">
      <div className="eyebrow">Current authoritative book · U + Q ≤ L</div>
      {keys.map((key) => {
        const m = metrics[key],
          u = units[key];
        return (
          <div
            className="budget-dimension"
            key={key}
            data-testid={"budget-" + key}
          >
            <div className="budget-title">{u?.label || key}</div>
            <div className="budget-figures">
              <div>
                <span>{u?.used || "Used"} · U</span>
                <strong>{u?.format(m.used) ?? m.used}</strong>
              </div>
              <div>
                <span>Reserved · Q</span>
                <strong>{u?.format(m.reserved) ?? m.reserved}</strong>
              </div>
            </div>
            <div
              className="budget-meter"
              aria-label={`${m.used} used, ${m.reserved} reserved, ${m.limit} limit`}
            >
              <i
                style={{ width: `${m.limit ? (100 * m.used) / m.limit : 0}%` }}
              />
              <i
                className="held"
                style={{
                  width: `${m.limit ? (100 * m.reserved) / m.limit : 0}%`,
                }}
              />
            </div>
            <div className="small muted">
              {u?.format(m.available) ?? m.available} available /{" "}
              {u?.format(m.limit) ?? m.limit} limit
            </div>
          </div>
        );
      })}
    </div>
  );
}
