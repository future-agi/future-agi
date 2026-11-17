// Sandbox snippets (options-truth L1 G2). Two things:
//  1. The Future AGI sandbox settings (Where it runs › Future AGI sandbox):
//     SANDBOX_DEFAULTS, SOURCES, RUNTIMES, sandboxYaml(config).
//  2. sandbox_server.py: the runnable local sandbox for the task suite
//     (stdlib only), the drawer's step "Start the sandbox" and
//     examples/experiments/r4/tools/sandbox_server.py. It serves the
//     suite's 12 tools, /reset, /state and /health on 127.0.0.1:8765.
//   steps({entry, mode, form, lang, ids}) → [{n, id, title, lang, code}]
// Pure.
import { RUNNER_FILES, lit } from "./lines";

export const SANDBOX_PORT = 8765;
export const SANDBOX_FILE = RUNNER_FILES.sandbox;
export const SANDBOX_COMMAND = `python ${SANDBOX_FILE} --port ${SANDBOX_PORT}`;
export const SANDBOX_HEALTH_URL = `http://127.0.0.1:${SANDBOX_PORT}/health`;

export const SANDBOX_DEFAULTS = Object.freeze({
  source: "repo",
  repo: "acme/support-agents",
  branch: "main",
  image: "ghcr.io/acme/support-agent:latest",
  zip: null,
  entry: "agent.py:graph",
  setup: "pip install -r requirements.txt",
  runtime: "python3.12",
  network: "off",
  allowlist: ["api.acme.ai"],
  cpu: 2,
  memory_gb: 4,
  timeout_s: 900,
});

/**
 * options-truth L2 G4 fix 9 (S5): the sandbox runs at most min(2 × vCPU,
 * 10) tries at a time.
 * @param {number} cpu  the sandbox's vCPU
 */
export function sandboxAtATimeMax(cpu) {
  const n = Number(cpu);
  const v = Number.isFinite(n) && n > 0 ? n : SANDBOX_DEFAULTS.cpu;
  return Math.max(1, Math.min(2 * v, 10));
}

/**
 * The limits a run uses under a cap (pure, S5 / I3): `concurrencyMax` is
 * the smallest cap that applies and can be named (the sandbox's min(2 ×
 * vCPU, 10), or the connected agent's declared cap on its machine), the
 * value is clamped to it, `requestedConcurrency` keeps what the user set,
 * `capLine` says which cap it is and `clampReason` names a clamp. A cap
 * that cannot be named is never applied (no silent clamp).
 * @param {object} limits  section 4's limits
 * @param {{sandboxCpu?: number|null, agentName?: string|null,
 *   machineName?: string|null}} o  sandboxCpu: set in sandbox mode only;
 *   the agent's cap (limits.concurrencyMax) applies only with agentName
 */
export function cappedLimits(
  limits,
  { sandboxCpu = null, agentName = null, machineName = null } = {},
) {
  const l = limits ?? {};
  // A source config that owns run-at-a-time (voice) sets it directly.
  const bySource = (l.setBySource ?? []).includes("concurrency");
  const asked =
    (bySource ? null : Number(l.requestedConcurrency)) ||
    Number(l.concurrency) ||
    1;
  const agentMax = agentName ? Number(l.concurrencyMax) || null : null;
  const sbMax = sandboxCpu != null ? sandboxAtATimeMax(sandboxCpu) : null;
  const caps = [
    sbMax != null ? { max: sbMax, who: "The sandbox" } : null,
    agentMax != null
      ? {
          max: agentMax,
          who: `${agentName}${machineName ? ` on ${machineName}` : ""}`,
        }
      : null,
  ].filter(Boolean);
  if (!caps.length)
    return {
      ...l,
      concurrency: asked,
      requestedConcurrency: asked,
      concurrencyMax: null,
      capLine: null,
      clampReason: null,
    };
  // The lowest cap wins; on a tie the sandbox is named (it is the outer one).
  const cap = caps.reduce((a, b) => (b.max < a.max ? b : a));
  const clamped = asked > cap.max;
  const line = `${cap.who} runs at most ${cap.max} at a time`;
  return {
    ...l,
    concurrency: Math.min(asked, cap.max),
    requestedConcurrency: asked,
    concurrencyMax: cap.max,
    capLine: `${line}${clamped ? ` (you set ${asked})` : ""}`,
    clampReason: clamped ? `${line} (you set ${asked})` : null,
  };
}

export const RUNTIMES = Object.freeze([
  { id: "python3.12", label: "Python 3.12" },
  { id: "node22", label: "Node 22" },
]);

export const SOURCES = Object.freeze([
  { id: "image", label: "Image" },
  { id: "repo", label: "Repo" },
  { id: "zip", label: "Zip" },
]);

export const NETWORKS = Object.freeze([
  { id: "off", label: "Off" },
  { id: "allowlist", label: "Allowlist" },
  { id: "open", label: "Open" },
]);

export const RESOURCES = Object.freeze([
  [1, 2],
  [2, 4],
  [4, 8],
  [8, 16],
]);

/** `acme/support-agents@main` / `support-agent.zip` / the image. */
export function sourceLine(c) {
  if (c.source === "zip") return c.zip?.name || "a .zip of your project folder";
  if (c.source === "image") return c.image;
  return `${c.repo}@${c.branch}`;
}

/** `2 vCPU / 4 GB`. */
export const resourcesLine = (c) => `${c.cpu} vCPU / ${c.memory_gb} GB`;

/** Allowlist hosts from a text field or a list. */
export const hostsOf = (v) =>
  (Array.isArray(v) ? v : String(v ?? "").split(/[\s,]+/))
    .map((h) => h.trim())
    .filter(Boolean);

export function sandboxYaml(c = SANDBOX_DEFAULTS) {
  const src =
    c.source === "image"
      ? [`  image: ${c.image}`]
      : c.source === "zip"
        ? [`  upload: ${c.zip?.name || "project.zip"}`]
        : [`  repo: ${c.repo}`, `  branch: ${c.branch}`];
  const hosts = hostsOf(c.allowlist);
  return [
    "# The Future AGI sandbox settings of this run (Where it runs › Future AGI sandbox)",
    "sandbox:",
    ...src,
    `  entry: ${c.entry}`,
    `  setup: ${c.setup}`,
    `  runtime: ${c.runtime}`,
    `  network: ${c.network === "allowlist" ? `allowlist [${hosts.join(", ")}]` : c.network}`,
    `  resources: {cpu: ${c.cpu}, memory_gb: ${c.memory_gb}, timeout_s: ${c.timeout_s ?? 900}}`,
    "  model_keys: workspace_secrets",
    "",
  ].join("\n");
}

/**
 * sandbox_server.py: a local stand-in for the suite's sandbox (stdlib). The
 * tools answer from the start_state each /reset sends; write tools record
 * their call, which /state returns for the end-state checks.
 * @param {{tools?: string[], name?: string}} sb
 */
export function sandboxServerScript(sb = {}) {
  const tools = sb.tools?.length
    ? sb.tools
    : [
        "lookup_order",
        "lookup_customer",
        "issue_refund",
        "issue_store_credit",
        "update_address",
        "cancel_item",
        "schedule_callback",
        "create_ticket",
        "escalate_to_human",
        "search_kb",
        "send_email",
        "apply_discount",
      ];
  return [
    `# ${SANDBOX_FILE} · a local ${sb.name ?? "acme-support-sandbox"} for the task suite (stdlib only).`,
    `#   ${SANDBOX_COMMAND}`,
    "#   export ACME_SANDBOX_URL=http://127.0.0.1:8765",
    "# POST /reset {start_state} → {session_id} · POST /tools/<name> {args} → JSON",
    "# GET /state?session_id=… → the end state · GET /health → {ok, tools}",
    "import argparse",
    "import json",
    "import threading",
    "import urllib.request",
    "import uuid",
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer",
    "from urllib.parse import parse_qs, urlparse",
    "",
    "TOOLS = [",
    ...tools.map((t) => `    ${lit(t)},`),
    "]",
    "SESSIONS = {}",
    "",
    "",
    "def answer_tool(state, tool, args):",
    '    """Lookups read the start_state; every other tool records its call."""',
    '    verb, _, noun = tool.partition("_")',
    '    rows = state.get(f"{noun}s", [])',
    '    hit = [r for r in rows if r.get("id") in args.values()]',
    '    if verb in ("lookup", "search"):',
    '        return hit[0] if hit else {"found": False}',
    '    state.setdefault("calls", []).append({"tool": tool, "args": args})',
    '    return {"ok": True, "tool": tool}',
    "",
    "",
    "class Sandbox(BaseHTTPRequestHandler):",
    "    def body(self):",
    '        size = int(self.headers.get("Content-Length") or 0)',
    '        return json.loads(self.rfile.read(size) or b"{}")',
    "",
    "    def send(self, payload, status=200):",
    "        self.send_response(status)",
    '        self.send_header("Content-Type", "application/json")',
    '        self.send_header("Access-Control-Allow-Origin", "*")',
    "        self.end_headers()",
    "        self.wfile.write(json.dumps(payload).encode())",
    "",
    "    def do_OPTIONS(self):  # the drawer's Check sandbox calls from a browser",
    "        self.send({})",
    "",
    "    def do_GET(self):",
    "        url = urlparse(self.path)",
    '        if url.path == "/health":',
    '            return self.send({"ok": True, "tools": len(TOOLS)})',
    '        if url.path == "/state":',
    '            session = parse_qs(url.query).get("session_id", [""])[0]',
    "            return self.send(SESSIONS.get(session, {}))",
    '        self.send({"error": "not found"}, 404)',
    "",
    "    def do_POST(self):",
    "        url = urlparse(self.path)",
    "        args = self.body()",
    '        if url.path == "/reset":',
    "            session = uuid.uuid4().hex",
    '            SESSIONS[session] = dict(args.get("start_state") or {})',
    '            return self.send({"session_id": session})',
    '        tool = url.path.rsplit("/", 1)[-1]',
    "        if tool not in TOOLS:",
    '            return self.send({"error": f"unknown tool {tool}"}, 404)',
    '        session = self.headers.get("X-Session-Id") or next(iter(SESSIONS), "")',
    "        self.send(answer_tool(SESSIONS.setdefault(session, {}), tool, args))",
    "",
    "    def log_message(self, fmt, *args):",
    '        print(f"{self.command} {self.path}")',
    "",
    "",
    "def main():",
    "    parser = argparse.ArgumentParser(description=__doc__)",
    `    parser.add_argument("--port", type=int, default=${SANDBOX_PORT})`,
    '    parser.add_argument("--host", default="127.0.0.1")',
    '    parser.add_argument("--check", action="store_true", help="start, answer, stop")',
    "    args = parser.parse_args()",
    "    server = ThreadingHTTPServer((args.host, args.port), Sandbox)",
    '    print(f"{len(TOOLS)} tools on http://{args.host}:{server.server_port}")',
    "    if args.check:  # one request to itself, then exit (CI and dry runs)",
    "        threading.Thread(target=server.handle_request, daemon=True).start()",
    '        url = f"http://{args.host}:{server.server_port}/health"',
    "        print(urllib.request.urlopen(url, timeout=5).read().decode())",
    "        return",
    "    server.serve_forever()",
    "",
    "",
    'if __name__ == "__main__":',
    "    main()",
    "",
  ].join("\n");
}

export function steps({ ids = {} } = {}) {
  return [
    {
      n: 1,
      id: "sandbox",
      title: "Sandbox settings",
      lang: "yaml",
      code: sandboxYaml({ ...SANDBOX_DEFAULTS, ...(ids.sandboxConfig ?? {}) }),
    },
  ];
}
