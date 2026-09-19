export type Json = any;
export type Risk = {
  used_cents: number;
  reserved_cents: number;
  limit_cents: number;
  available_cents: number;
};
export type Event = {
  seq: number;
  run_id: string;
  kind: string;
  ts: number;
  data: Json;
  hash: string;
  prev_hash: string;
};
export type Run = {
  id: string;
  proposal: Json;
  execution_proposal?: Json;
  status: string;
  snapshot?: Json;
  snapshot_hash?: string;
  decision?: Json;
  kappa?: Json;
  receipt?: Json;
  socket_decision?: Json;
  execution?: Json;
  events?: Event[];
  reservation_id?: string;
  approval?: Json;
};
export type View = {
  now: number;
  state: Json;
  risk: Risk;
  runs: Run[];
  reservations: Json[];
  grants: Json[];
  payments: Json[];
  keys: Json;
};
export type Lab = {
  book_id: string;
  scenario: string;
  title: string;
  explanation: string;
  run: Run;
  view: View;
  verification: Json;
  race?: Json;
  dispatch_run?: Run;
};
export const money = (c: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    c / 100,
  );
export const short = (s?: string) =>
  s ? s.slice(0, 10) + "…" + s.slice(-5) : "—";
