import { mockFor } from "./mock";

// The console reads every value from /api/* (nginx proxies it to the api gateway).
const DEV = process.env.NODE_ENV !== "production";

// GET one API path. In `next dev` only, a failed call returns the design-review mock.
// The production export never uses the mock, so the deployed console shows live data only.
export async function getJSON(path) {
  try {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path} ${r.status}`);
    return await r.json();
  } catch (e) {
    if (DEV) {
      const m = mockFor(path);
      if (m !== undefined) return m;
    }
    throw e;
  }
}

// A state-changing call. A 401 means the viewer account clicked an operator control.
export async function send(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const why = r.status === 401 ? "operator login required" : (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j));
    throw new Error(`${r.status}: ${why}`);
  }
  return j;
}

export { DEV };
