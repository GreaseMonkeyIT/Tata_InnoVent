// alert-dispatcher: polls DB thresholds every 10s, POSTs alerts to notify-gateway.
// DELIBERATE ANTI-PATTERN: 3x immediate retry, no backoff -> retry amplification (S4).
const { Client } = require("pg");
const NOTIFY = process.env.NOTIFY_URL || "http://notify-gateway.factory-edge.svc:8080/alert";
const DSN = process.env.PG_DSN || "postgres://factory:factory@timescaledb.factory-data.svc/telemetry";

async function send(body, attempt = 1) {
  try {
    const r = await fetch(NOTIFY, { method: "POST", body: JSON.stringify(body), signal: AbortSignal.timeout(1000) });
    if (!r.ok) throw new Error("status " + r.status);
  } catch (e) {
    console.log(`notify failed (attempt ${attempt}):`, e.message);
    if (attempt < 3) return send(body, attempt + 1); // no backoff, on purpose
  }
}

async function poll() {
  const c = new Client({ connectionString: DSN, connectionTimeoutMillis: 3000 });
  try {
    await c.connect();
    const r = await c.query("SELECT count(*) n FROM readings WHERE ts > now() - interval '1 minute'");
    const n = +r.rows[0].n;
    if (n < 1000) await send({ alert: "ingest-rate-low", n });   // fires when ingest stalls (S1!)
    if (n > 150000) await send({ alert: "ingest-rate-flood", n });
  } catch (e) {
    console.log("db poll error:", e.message);
    await send({ alert: "db-unreachable", err: e.message });
  } finally { await c.end().catch(() => {}); }
}
require("http").createServer((q, s) => s.end("ok")).listen(8080); // healthz
setInterval(poll, 10000);
console.log("alert-dispatcher up");
