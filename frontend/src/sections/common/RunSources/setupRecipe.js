// IT-R5 G1 P3: the code path's one recipe (pure). `Run a script` reads:
// a 30 s no-keys line first, ONE `Copy all setup commands` block (install,
// keys, sandbox start + check, the 1-task command; keys masked on screen,
// real on Copy), the runner card's order line, and step 4's two-line
// run_rows() diff plus a tool wrapper that routes a tool to the sandbox.

const withLimit = (cmd) =>
  !cmd ? null : /--limit\b/.test(cmd) ? cmd : `${cmd} --limit 1`;

/**
 * `python run_and_upload.py … --dry-run [--mock-tools] --limit 1`: needs no
 * keys; with a sandbox it uses recorded tool answers (no docker either).
 * @param {{dryRunCommand?: string, mockCommand?: string}} snippet
 * @param {(cmd: string) => string} [withFlag]  adds `--no-sandbox` when set
 */
export function tryNoKeysCommand(snippet, opt = {}) {
  // `{noSandbox}` (or the legacy withFlag function): skip the sandbox →
  // `--dry-run --no-sandbox --limit 1`, never --mock-tools with it.
  const noSandbox =
    typeof opt === "function" ? opt("x") !== "x" : Boolean(opt?.noSandbox);
  const base = noSandbox
    ? snippet?.noSandboxCommand ?? snippet?.dryRunCommand ?? null
    : snippet?.mockCommand ?? snippet?.dryRunCommand ?? null;
  return base ? withLimit(base) : null;
}

/**
 * The setup as one script. `shown` masks the keys (screen), `copy` has
 * them (clipboard). Sections are commented so the pasted script reads.
 * @returns {{shown: string, copy: string, hasKeys: boolean}}
 */
export function copyAllSetup({
  install,
  sandbox,
  tryCommand,
  noSandbox = false,
  rowNoun = "task",
  needs = [],
}) {
  const need = needsLine(needs);
  const parts = (keys) =>
    [
      need,
      install
        ? [
            "# 1. Install and set keys",
            keys ? install.copyText ?? install.code : install.code,
          ].join("\n")
        : null,
      sandbox && !noSandbox
        ? ["# 2. Start the sandbox and check it answers", sandbox.code].join(
            "\n",
          )
        : null,
      tryCommand
        ? [
            `# ${sandbox && !noSandbox ? 3 : 2}. Run 1 ${rowNoun}`,
            tryCommand,
          ].join("\n")
        : null,
    ]
      .filter(Boolean)
      .join("\n\n");
  return {
    shown: parts(false),
    copy: `${parts(true)}\n`,
    hasKeys: Boolean(install?.copyText && install.copyText !== install.code),
  };
}

/**
 * The files the pasted setup expects next to it (L3 G4 C4), by their step
 * numbers: `# needs run_and_upload.py, sandbox_server.py and agent.py in
 * this folder (steps 1, 3, 4)`. `needs` = [{file, n}]; empty → null.
 */
export function needsLine(needs = []) {
  const list = (needs ?? []).filter((x) => x?.file);
  if (!list.length) return null;
  const files = list.map((x) => x.file);
  const names =
    files.length === 1
      ? files[0]
      : `${files.slice(0, -1).join(", ")} and ${files.at(-1)}`;
  const ns = list.map((x) => x.n).filter((n) => n != null);
  const where = ns.length
    ? ` (step${ns.length === 1 ? "" : "s"} ${ns.join(", ")})`
    : "";
  return `# needs ${names} in this folder${where}`;
}

/**
 * The runner card's order line (matches the script: it POSTs to
 * `/runs/{RUN_ID}/results/`, which answers 404 until the run is added).
 */
export function orderLine({ runLabel } = {}) {
  return `Add the run to the experiment first; the script uploads into it (RUN_ID${
    runLabel ? `, run ${runLabel}` : ""
  }). Run it before adding and the upload is rejected with "404: run not found".`;
}

const CALL_RE = /^\s*(const |let )?out = /;
const ANSWER_RE =
  /^\s*(const |let )?(output|\[output, error\]|output, error)\b/;

/**
 * The two lines of run_rows() / runRows() that call the agent and read its
 * answer, and the same two lines pointed at "your agent".
 * @returns {{before: string[], after: string[], fn: string}|null}
 */
export function agentDiff(file, lang = "python") {
  const ts = lang === "typescript";
  const lines = String(file ?? "").split("\n");
  const at = lines.findIndex((l) => CALL_RE.test(l));
  if (at < 0) return null;
  const ans = lines.slice(at + 1, at + 4).find((l) => ANSWER_RE.test(l));
  const before = [lines[at].trim(), ans ? ans.trim() : null].filter(Boolean);
  const after = ts
    ? [
        "const out = await myAgent.run(text); // your call",
        "output = out.reply; // the field with the answer",
      ]
    : [
        "out = my_agent.run(text)  # your call",
        'output, error = out["reply"], None  # the field with the answer',
      ];
  return {
    before,
    after: after.slice(0, before.length),
    fn: ts ? "runRows()" : "run_rows()",
  };
}

/**
 * A 6-line wrapper that routes one tool to the sandbox
 * (`$ACME_SANDBOX_URL/tools/<name>`), so end-state checks can read it.
 */
export function toolWrapper({ envVar, tool, lang = "python" } = {}) {
  const name = tool || "lookup_order";
  const env = envVar || "SANDBOX_URL";
  if (lang === "typescript")
    return [
      `const SANDBOX = process.env.${env};`,
      `export async function ${camel(name)}(args) {`,
      `  const r = await fetch(\`\${SANDBOX}/tools/${name}\`, {`,
      `    method: "POST", body: JSON.stringify(args) });`,
      "  return r.json();",
      "}",
    ].join("\n");
  return [
    "import os, requests",
    `SANDBOX = os.environ["${env}"]`,
    "",
    `def ${name}(**args):`,
    `    r = requests.post(f"{SANDBOX}/tools/${name}", json=args, timeout=30)`,
    "    return r.json()",
  ].join("\n");
}

function camel(s) {
  return String(s).replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

const SETUP_IDS = ["save", "install", "sandbox", "agent"];

/**
 * The setup steps still missing before the 1-row command can run (L3 G4
 * C5): save the runner, install/keys, sandbox (unless skipped), agent;
 * each with its real number and title. `titles` = {id: title}.
 * @returns {{n: number, id: string, title: string}[]}
 */
export function missingSteps(
  done = {},
  { noSandbox = false, steps = [], titles = {} } = {},
) {
  return steps
    .map((id, i) => ({ n: i + 1, id, title: titles?.[id] ?? id }))
    .filter(
      (s) =>
        SETUP_IDS.includes(s.id) &&
        !done[s.id] &&
        !(s.id === "sandbox" && noSandbox),
    );
}

/** Steps 1-4 (save, install/keys, sandbox, agent) all done (or skipped). */
export function setupReady(done = {}, opts = {}) {
  return missingSteps(done, opts).length === 0;
}

/**
 * The note after `Copy 1-row command` (L3 G4 C5): the missing steps by
 * number and title; else, before the run is added, `Add to experiment
 * first`; else where the row lands.
 */
export function copyTryNote({ missing = [], added = false, runLabel } = {}) {
  if (missing.length) {
    const list = missing.map((s) => `step ${s.n} (${s.title})`);
    const names =
      list.length === 1
        ? list[0]
        : `${list.slice(0, -1).join(", ")} and ${list.at(-1)}`;
    return `Copied. Do ${names} first`;
  }
  const where = runLabel ? `run ${runLabel}` : "this run";
  return added
    ? `Copied. Run it in your shell; the row lands in ${where}`
    : `Copied. Add to experiment first; then run it in your shell and the row lands in ${where}`;
}
