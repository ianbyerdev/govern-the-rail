import type { Json } from "./types";
export type Domain = "payments" | "trading" | "referrals" | "runtime";
export const domains: Record<
  Domain,
  {
    label: string;
    title: string;
    subtitle: string;
    audience: string;
    unit: string;
  }
> = {
  payments: {
    label: "Payments",
    title: "Pay one exact beneficiary",
    subtitle:
      "A proposal becomes a reserved, single-use authority to transfer value.",
    audience: "PAYMENTS-1",
    unit: "amount_cents",
  },
  trading: {
    label: "Trading OMS / EMS",
    title: "From an order to an accountable fill",
    subtitle:
      "Order acceptance, fills and cancellation each leave distinct evidence.",
    audience: "OMS-17",
    unit: "notional_usd_cents",
  },
  referrals: {
    label: "Patient referrals",
    title: "Release a referral, exactly as reviewed",
    subtitle:
      "Patient, sealed records, recipient, purpose and consent travel together.",
    audience: "REFERRAL-EGRESS-1",
    unit: "records",
  },
  runtime: {
    label: "Agent runtime",
    title: "A script cannot grant itself authority",
    subtitle:
      "Explore protected gates, narrowed grants and real OS containment.",
    audience: "RUNTIME-1",
    unit: "job_starts",
  },
};
export const units: Record<
  string,
  { label: string; used: string; format: (n: number) => string }
> = {
  amount_cents: {
    label: "Cumulative payments",
    used: "Settled",
    format: (n) => currency(n),
  },
  notional_usd_cents: {
    label: "Cumulative buy notional",
    used: "Filled",
    format: (n) => currency(n),
  },
  records: {
    label: "Disclosed records",
    used: "Disclosed",
    format: (n) => String(n),
  },
  bytes: {
    label: "Disclosed bytes",
    used: "Disclosed",
    format: (n) => `${n.toLocaleString()} B`,
  },
  job_starts: {
    label: "Cumulative job starts",
    used: "Started",
    format: (n) => String(n),
  },
  job_slots: {
    label: "Concurrent job slots",
    used: "Realized",
    format: (n) => String(n),
  },
};
export function currency(c: number) {
  return (c / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
}
export const policyFields = [
  "version",
  "session_limit_cents",
  "per_action_cents",
  "approval_above_cents",
  "max_ttl_seconds",
  "allow_attenuation",
  "beneficiaries",
  "currencies",
  "routes",
  "purposes",
  "limits",
  "per_action",
  "approval_above",
  "resources",
  "recipients",
  "price_min_cents",
  "price_max_cents",
];
export function policyConfig(pack: Json): Json {
  return Object.fromEntries(
    Object.entries(pack).filter(([key]) => policyFields.includes(key)),
  );
}
export function effectTitle(e: Json) {
  if (!e) return "Awaiting authoritative resolution";
  const x = e.x;
  if (e.o === "payment")
    return `${currency(x.amount_cents)} ${x.currency} → ${e.r === "beneficiary:acme" ? "Acme Research" : e.r}`;
  if (e.o === "order.submit")
    return `Buy ${x.quantity.toLocaleString()} XYZ · limit ${currency(x.limit_price_cents)}`;
  if (e.o === "order.cancel") return `Cancel ${x.order_id}`;
  if (e.o === "referral.release")
    return `${x.manifest.length} synthetic records → ${x.recipient === "recipient:north" ? "North Clinic" : x.recipient}`;
  if (e.o === "job.delegate") return `Delegate to ${x.agent_id}`;
  if (e.o === "dispatch") return `Delegate to ${x.agent_id}`;
  return "Run audit.py · constrained workspace";
}
export const faults: Record<Domain, [string, string][]> = {
  payments: [
    ["none", "Normal path"],
    ["tamper", "Increase amount after κ"],
    ["substitute", "Substitute beneficiary"],
    ["audience", "Wrong audience"],
    ["expiry", "Expired capability"],
    ["stale", "Stale resource"],
    ["pack", "Supersede pack"],
    ["alias", "Equivalent alias"],
    ["rebind", "Rebind alias"],
    ["lost", "Lose execution receipt"],
  ],
  trading: [
    ["none", "Normal path"],
    ["tamper", "Increase quantity after κ"],
    ["route", "Substitute route"],
    ["audience", "Wrong audience"],
    ["expiry", "Expired capability"],
    ["stale", "Stale instrument"],
    ["pack", "Supersede pack"],
    ["alias", "Equivalent instrument alias"],
    ["lost", "Lose order receipt"],
  ],
  referrals: [
    ["none", "Normal path"],
    ["tamper", "Append unreviewed document"],
    ["substitute", "Substitute recipient"],
    ["patient", "Substitute patient"],
    ["consent", "Revoke consent"],
    ["document", "Change sealed record"],
    ["audience", "Wrong audience"],
    ["expiry", "Expired capability"],
    ["pack", "Supersede pack"],
    ["lost", "Lose disclosure receipt"],
  ],
  runtime: [
    ["none", "Normal path"],
    ["tamper", "Add network access after κ"],
    ["audience", "Wrong audience"],
    ["expiry", "Expired capability"],
    ["stale", "Stale workspace"],
    ["pack", "Supersede pack"],
    ["lost", "Lose job exit receipt"],
    ["before_dispatch", "Pause before dispatch"],
    ["after_claim", "Interrupt after launch claim"],
    ["after_run", "Interrupt before exit receipt"],
  ],
};
