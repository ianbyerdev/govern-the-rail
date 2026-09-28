import type { Json } from "../types";
import { effectTitle } from "../domains";
export function EffectCard({
  effect,
  review = false,
}: {
  effect: Json;
  review?: boolean;
}) {
  if (!effect)
    return (
      <div className="effect-summary pending">
        <div className="eyebrow">Authoritative resolver</div>
        <h3>Resolve the action first</h3>
        <p>Names, targets and state bindings come from the institution.</p>
      </div>
    );
  const states = Object.entries(effect.c).filter(
    ([k, v]) => k.endsWith("_version") && typeof v !== "object",
  );
  return (
    <div className={"effect-summary " + (review ? "review-grid" : "")}>
      <div className="eyebrow">
        {review
          ? "Immutable snapshot for human review"
          : "Resolved exact effect"}
      </div>
      <h3>{effectTitle(effect)}</h3>
      <div className="canonical-target">{effect.r}</div>
      {effect.x.route && <p className="small muted">Route: {effect.x.route}</p>}
      {effect.x.purpose && (
        <p className="small muted">Purpose: {effect.x.purpose}</p>
      )}
      <p className="state-summary">
        {states
          .map(([k, v]) => `${k.replace("_version", "")} v${v}`)
          .join(" · ")}
        {effect.c.consent &&
          `${states.length ? " · " : ""}Consent v${effect.c.consent.version} · ${effect.c.consent.active ? "active" : "revoked"}`}
      </p>
      {review && (
        <p className="small muted">
          Principal: {effect.p}
          <br />
          Audience: {effect.s}
          <br />
          Operation: {effect.o}
        </p>
      )}
      <details className="effect-bindings" key={effect.o + String(review)}>
        <summary>Principal, audience &amp; all effect bindings</summary>
        <dl className="bindings">
          <dt>p · Principal</dt>
          <dd>{effect.p}</dd>
          <dt>s · Socket</dt>
          <dd>{effect.s}</dd>
          <dt>o · Operation</dt>
          <dd>{effect.o}</dd>
          <dt>r · Resource</dt>
          <dd>{effect.r}</dd>
          <dt>x · Arguments</dt>
          <dd>
            <pre>{JSON.stringify(effect.x, null, 2)}</pre>
          </dd>
          <dt>c · State</dt>
          <dd>
            <pre>{JSON.stringify(effect.c, null, 2)}</pre>
          </dd>
        </dl>
      </details>
      {effect.x.manifest && (
        <div className="manifest">
          <strong>Sealed document manifest</strong>
          {effect.x.manifest.map((d: Json) => (
            <div key={d.id}>
              <span>
                {d.id} · v{d.version}
              </span>
              <span>{d.bytes} B</span>
            </div>
          ))}
          <p>
            Purpose: {effect.x.purpose}
            <br />
            Destination: {effect.x.endpoint}
          </p>
        </div>
      )}
    </div>
  );
}
