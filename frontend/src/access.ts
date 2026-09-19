export type Access = {
  mode: "operator" | "visitor";
  token?: string;
  session_id?: string;
  expires_at?: number;
  limits?: { lifetime_seconds: number; max_population: number };
};

export async function request(
  path: string,
  access?: Access,
  body?: unknown,
  method?: string,
) {
  const response = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(access?.mode === "operator"
        ? { Authorization: `Bearer ${access.token}` }
        : { "X-SAAC-Demo": "1" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401 && access) {
      window.dispatchEvent(
        new CustomEvent("saac-session-ended", {
          detail: { mode: access.mode, session_id: access.session_id },
        }),
      );
    }
    const detail = value.detail || value;
    throw Error(
      typeof detail === "string"
        ? detail
        : detail.message || JSON.stringify(detail),
    );
  }
  return value;
}
