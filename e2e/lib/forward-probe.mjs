// Active probes for a runner's loopback forwarder (lib/loopback-forwarder.ts, which
// runs this as a child process: the inspection is synchronous). Input on stdin:
//   { context, credentials: { pg?, ch? }, targets: [{ kind, service, hostPort, hosts,
//     container, probes }] }
// For each target it sends `probes` fresh connections through the forwarder, spread
// evenly over `hosts` (its loopback listeners), each carrying its own random nonce,
// then confirms from the inspected container's own side - docker exec or docker
// logs, never through the forwarder - that each one arrived there and, for every
// kind but the frontend, that the answer is one only that container could give:
//   - mock-llm, webhook-sink, mock-slack (the harness's own mocks, e2e/stack): a
//     fresh key per inspection is planted inside the container (docker exec, over
//     stdin) and each probe must be answered with exactly HMAC-SHA256(key, nonce);
//   - postgres: the backend pid the session answers is the one the container lists
//     for its nonce; clickhouse: the per-probe secret file written into the container;
//   - frontend: ARRIVAL ONLY. It is the product's own nginx, which has no route that
//     could answer with a nonce-bound value without a product change, so a forwarder
//     that delivers each probe but replaces or replays what comes back is not
//     detected there. lib/loopback-forwarder.ts records it in the receipt (kind
//     arrival-only, with the reason), a known limitation for acceptance.
// Output on stdout, counts only:
//   [{ service, hostPort, sent, delivered }]
// Nonces, keys, answers, pids and addresses never leave this process. A probe that
// cannot connect, answers wrongly, or has no evidence is simply not delivered; any
// failure of the evidence reads counts none of that target's probes delivered.
import { execFile, spawn } from "node:child_process";
import { createHmac, randomBytes } from "node:crypto";
import http from "node:http";

const OP_TIMEOUT_MS = 5_000;
const DEADLINE_MS = 120_000;
const LABEL = /^fwdprobe-[0-9a-f]{32}$/;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const nonce = () => `fwdprobe-${randomBytes(16).toString("hex")}`;

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const plan = JSON.parse(raw);
const deadline = setTimeout(() => {
  process.stderr.write("forward probes passed their deadline\n");
  process.exit(1);
}, DEADLINE_MS);

const docker = (args, timeout = 30_000) =>
  new Promise((resolve, reject) => {
    execFile(
      "docker",
      ["--context", plan.context, ...args],
      { encoding: "utf8", timeout, maxBuffer: 64 * 1024 * 1024 },
      (error, stdout) =>
        error
          ? reject(new Error("docker evidence read failed"))
          : resolve(stdout),
    );
  });

/** docker with its input on stdin (probe keys and secrets never go on a command line). */
const dockerWithInput = (args, input, timeout = 30_000) =>
  new Promise((resolve, reject) => {
    const child = spawn("docker", ["--context", plan.context, ...args], {
      stdio: ["pipe", "pipe", "ignore"],
    });
    let out = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), timeout);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      out += chunk;
    });
    child.on("error", () => {
      clearTimeout(timer);
      reject(new Error("docker evidence write failed"));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error("docker evidence write failed"));
    });
    child.stdin.end(input);
  });

/** One HTTP exchange on its own connection (no keep-alive, no agent). */
function request(
  host,
  port,
  path,
  { method = "GET", headers = {}, body } = {},
) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host,
        port,
        path,
        method,
        agent: false,
        timeout: OP_TIMEOUT_MS,
        headers: {
          connection: "close",
          ...headers,
          ...(body === undefined
            ? {}
            : { "content-length": Buffer.byteLength(body) }),
        },
      },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          if (data.length < 65_536) data += chunk;
        });
        res.on("end", () => resolve({ status: res.statusCode, body: data }));
        res.on("error", reject);
      },
    );
    req.on("timeout", () => req.destroy(new Error("probe timed out")));
    req.on("error", reject);
    req.end(body);
  });
}

/** The probe hosts in order: `probes` of them, the listeners taking turns. */
const spread = (target) =>
  Array.from(
    { length: target.probes },
    (_, i) => target.hosts[i % target.hosts.length],
  );

// The harness mocks (e2e/stack/{mock-llm,webhook-sink,mock-slack}/server.mjs) share one
// probe route. Per inspection and container, a fresh 32-byte key is written by docker
// exec into /tmp/fwdprobe-keys/<key id> inside that container (over stdin: never a
// command line or the environment; never sent through the forwarder). Each probe,
// GET /__probe/<key id>/<nonce>, must be answered with exactly HMAC-SHA256(key, nonce),
// computed here from the key: a replaced or replayed answer misses. Its arrival is
// read from inside the container (/__probed/<nonce>, node on its own listener,
// 127.0.0.1:8080 there) and must be exactly one. The key is removed after.
const PLANT_KEY =
  'case "$0" in fwdprobe-*) umask 077; mkdir -p /tmp/fwdprobe-keys && exec cat > "/tmp/fwdprobe-keys/$0" ;; *) exit 1 ;; esac';
const REMOVE_KEY =
  'case "$0" in fwdprobe-*) rm -f -- "/tmp/fwdprobe-keys/$0" ;; esac';
const ARRIVALS = `const counts = [];
for (const label of process.argv.slice(1)) {
  try {
    const body = await (await fetch('http://127.0.0.1:8080/__probed/' + label)).json();
    counts.push(Number.isInteger(body.count) ? body.count : -1);
  } catch { counts.push(-1); }
}
console.log(JSON.stringify(counts));`;

async function probeMock(target) {
  const keyId = nonce();
  const key = randomBytes(32);
  if (!LABEL.test(keyId)) return 0;
  try {
    await dockerWithInput(
      ["exec", "-i", target.container, "sh", "-c", PLANT_KEY, keyId],
      key.toString("hex"),
    );
    const probes = [];
    for (const host of spread(target)) {
      const label = nonce();
      const expected = createHmac("sha256", key).update(label).digest("hex");
      let answered = false;
      try {
        const { status, body } = await request(
          host,
          target.hostPort,
          `/__probe/${keyId}/${label}`,
        );
        answered = status === 200 && JSON.parse(body)?.answer === expected;
      } catch {
        answered = false;
      }
      probes.push({ label, answered });
    }
    if (!probes.every((p) => LABEL.test(p.label))) return 0;
    const counts = JSON.parse(
      await docker([
        "exec",
        target.container,
        "node",
        "--input-type=module",
        "-e",
        ARRIVALS,
        ...probes.map((p) => p.label),
      ]),
    );
    return probes.filter((p, i) => p.answered && counts[i] === 1).length;
  } finally {
    await docker([
      "exec",
      target.container,
      "sh",
      "-c",
      REMOVE_KEY,
      keyId,
    ]).catch(() => {});
  }
}

// Arrival only (see the header): the product's nginx answers the probe path however
// its config does, and nothing in that answer is bound to the nonce.
async function probeFrontend(target) {
  const since = new Date(Date.now() - 5_000).toISOString();
  const probes = [];
  for (const host of spread(target)) {
    const label = nonce();
    let answered = false;
    try {
      answered =
        (await request(host, target.hostPort, `/__e2e-forward-probe/${label}`))
          .status > 0;
    } catch {
      answered = false;
    }
    probes.push({ label, answered });
  }
  // nginx writes its access log to the container's stdout: the daemon's capture of it.
  let counts = [];
  for (let attempt = 0; attempt < 20; attempt++) {
    const log = await docker(["logs", "--since", since, target.container]);
    counts = probes.map(
      (p) =>
        log.split(`"GET /__e2e-forward-probe/${p.label} HTTP/1.1"`).length - 1,
    );
    if (counts.every((n, i) => n >= 1 || !probes[i].answered)) break;
    await sleep(250);
  }
  return probes.filter((p, i) => p.answered && counts[i] === 1).length;
}

async function probePostgres(target) {
  const credentials = plan.credentials?.pg;
  if (!credentials) return 0;
  const { default: pg } = await import("pg");
  const sessions = [];
  try {
    // Every session stays open until the container has been read: pg_stat_activity lists live sessions.
    for (const host of spread(target)) {
      const label = nonce();
      const client = new pg.Client({
        host,
        port: target.hostPort,
        user: credentials.user,
        password: credentials.password,
        database: credentials.database,
        application_name: label,
        connectionTimeoutMillis: OP_TIMEOUT_MS,
        query_timeout: OP_TIMEOUT_MS,
        statement_timeout: OP_TIMEOUT_MS,
      });
      client.on("error", () => {});
      const session = { label, client, pid: null };
      sessions.push(session);
      try {
        await client.connect();
        const { rows } = await client.query("SELECT pg_backend_pid() AS pid");
        session.pid = Number.isInteger(rows[0]?.pid) ? rows[0].pid : null;
      } catch {
        session.pid = null;
      }
    }
    const labels = sessions.map((s) => s.label);
    if (!labels.every((label) => LABEL.test(label))) return 0;
    const sql = `SELECT pid, application_name FROM pg_stat_activity WHERE application_name IN (${labels.map((l) => `'${l}'`).join(",")})`;
    // The container's own superuser over its local socket (its own environment, not the harness's).
    const out = await docker([
      "exec",
      target.container,
      "sh",
      "-c",
      'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -A -t -F "|" -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$0"',
      sql,
    ]);
    const seen = new Map();
    for (const line of out.split("\n")) {
      const [pid, label] = line.trim().split("|");
      if (label && LABEL.test(label))
        seen.set(label, [...(seen.get(label) ?? []), Number(pid)]);
    }
    return sessions.filter(
      (s) =>
        s.pid !== null &&
        JSON.stringify(seen.get(s.label)) === JSON.stringify([s.pid]),
    ).length;
  } finally {
    await Promise.all(sessions.map((s) => s.client.end().catch(() => {})));
  }
}

// ClickHouse: each probe gets its own secret, written by docker exec into the inspected
// container's user_files directory (over stdin, never a command line) and never sent
// through the forwarder; the probe asks for it by its file name with file(). Only a
// server reading that container's own files can answer it. No system log table is
// needed (the Standalone install removes query_log). The files are removed after.
const USER_FILES =
  'exec clickhouse extract-from-config --config-file "${CLICKHOUSE_CONFIG:-/etc/clickhouse-server/config.xml}" --key=user_files_path';
const WRITE_SECRETS =
  'umask 022; mkdir -p "$0" && cd "$0" && while read -r name value; do case "$name" in fwdprobe-*) printf %s "$value" > "$name" ;; esac; done';
const REMOVE_SECRETS =
  'cd "$0" && for name; do case "$name" in fwdprobe-*) rm -f -- "$name" ;; esac; done';

async function probeClickhouse(target) {
  const credentials = plan.credentials?.ch;
  if (!credentials) return 0;
  const probes = spread(target).map((host) => ({
    host,
    label: nonce(),
    secret: randomBytes(16).toString("hex"),
  }));
  if (!probes.every((p) => LABEL.test(p.label))) return 0;
  const dir = (
    await docker(["exec", target.container, "sh", "-c", USER_FILES])
  ).trim();
  if (!dir.startsWith("/") || /\s/.test(dir)) return 0;
  try {
    await dockerWithInput(
      ["exec", "-i", target.container, "sh", "-c", WRITE_SECRETS, dir],
      probes.map((p) => `${p.label} ${p.secret}\n`).join(""),
    );
    let delivered = 0;
    for (const p of probes) {
      try {
        const { status, body } = await request(p.host, target.hostPort, "/", {
          method: "POST",
          headers: {
            "x-clickhouse-user": credentials.user,
            "x-clickhouse-key": credentials.password,
            "content-type": "text/plain",
          },
          // One String row, printed as is plus a newline (the secret is hex).
          body: `SELECT * FROM file('${p.label}', 'RawBLOB') FORMAT TabSeparatedRaw`,
        });
        if (status === 200 && body.trim() === p.secret) delivered++;
      } catch {
        // not delivered
      }
    }
    return delivered;
  } finally {
    await docker([
      "exec",
      target.container,
      "sh",
      "-c",
      REMOVE_SECRETS,
      dir,
      ...probes.map((p) => p.label),
    ]).catch(() => {});
  }
}

const KINDS = {
  "mock-llm": probeMock,
  "webhook-sink": probeMock,
  "mock-slack": probeMock,
  frontend: probeFrontend,
  postgres: probePostgres,
  clickhouse: probeClickhouse,
};

const outcomes = [];
for (const target of plan.targets) {
  const run = KINDS[target.kind];
  const sent = run && target.hosts.length ? target.probes : 0;
  let delivered = 0;
  if (sent) {
    try {
      delivered = await run(target);
    } catch {
      delivered = 0;
    }
  }
  outcomes.push({
    service: target.service,
    hostPort: target.hostPort,
    sent,
    delivered,
  });
}
clearTimeout(deadline);
// Exit once written: a socket a forwarder never closed must not hold the inspection.
process.stdout.write(JSON.stringify(outcomes), () => process.exit(0));
