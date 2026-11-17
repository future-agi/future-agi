import { excludedToolsOf } from "./harnessTools";
export { excludedToolsOf };

// Harness snippets (MUST 1; options-truth L1 G3 / C4, C9, C14).
// Lane Ra's snippets/index.js dispatches here. Frozen signature:
//   steps({entry, mode, form, lang, ids}) → [{n, id, title, lang, code, note?}]
// plus:
//   harnessScript({entry, ids, recipe}) → run_harness_and_upload.py (complete)
//
// run_harness_and_upload.py follows the recipe the drawer shows, field by
// field: the model, the helper model (env), the permission level (CLI flag,
// env, or the wrapper's read-only / writable copy), every MCP server in the
// harness's own config file, the built-in tools, the instructions file where
// the harness reads it, the budget, what to collect, and the version's
// install line. It takes the runners' shared flags (S3): --run, --rows,
// --limit, --trials, --concurrency, --max-cost-usd, --timeout-s, --param,
// --permissions, --fail-under (the CI gate) and --dry-run. dataset integration FE-0:
// the canned CLI stand-in for --dry-run and the live connection are
// removed; the Agent harness card is absent until FE-1 replaces this file.
// Pure.
import { catalogEntry } from "../catalog";
import { FI_BASE_URL, commentLines, lit, runnerCommand } from "./lines";
import { keysStep } from "./python";
import {
  RECORD_FILES,
  TOKEN_PRICED,
  parserLines,
  parserOf,
} from "./harnessParsers";
import { MODEL_PRICES } from "../declaredUses";
import {
  PERMISSION_ORDER,
  RECORD_WORDS,
  asRecipe,
  claudeToolLists,
  collectOf,
  commandPreview,
  defaultRecipe,
  geminiCoreTools,
  geminiTurnsKey,
  harnessEnv,
  installLine,
  isOpenHandsV1,
  mcpArgv,
  permissionOf,
  profileOf,
  recordKeyOf,
} from "../recipe/recipeModel";

export const HARNESS_FILE = "run_harness_and_upload.py";
export const HARNESS_PACKAGES = "futureagi fi-instrumentation-otel requests";
export const UPLOAD_LINE =
  "Uploads results to POST /model-hub/experiments/v2/{experiment_id}/runs/{run_id}/results/";

/**
 * options-truth L2 finalize: the one-row command carries the drawer's flags
 * (ids.run, runFlagsOf: tries, at a time, cost cap, time limit, changed
 * params) like every other command the card prints, so the first row runs
 * under the same limits as the run (was a fixed `--workdir . --limit 1`,
 * which ran the try under the script's default time limit). Pure.
 * @param {{run?: object}} [ids]
 */
export function harnessTryCommand(ids = {}) {
  const f = ids?.run ?? null;
  return runnerCommand({
    lang: "harness",
    workdir: ".",
    limit: 1,
    ...(f
      ? {
          runId: f.runId,
          trials: f.trials,
          limits: f.limits,
          params: f.params,
        }
      : {}),
  });
}

function defaults(ids = {}) {
  return {
    datasetId: ids.datasetId ?? "",
    datasetName: ids.datasetName || "this dataset",
    experimentId: ids.experimentId ?? "",
    runId: ids.runId ?? "",
    runLabel: ids.runLabel || "#1A",
    inputColumn: ids.inputColumn || "input",
    tries: Math.max(1, Number(ids.tries) || 1),
  };
}

/** A python list literal, wrapped at 88 characters. */
function listLines(items, indent) {
  const pad = " ".repeat(indent);
  const out = [];
  let line = "";
  items.forEach((a) => {
    const tok = `${a},`;
    const next = line ? `${line} ${tok}` : `${pad}${tok}`;
    if (next.length > 88 && line) {
      out.push(line);
      line = `${pad}${tok}`;
    } else line = next;
  });
  if (line) out.push(line);
  return out;
}

/** `NAME = "…"`, split over lines in parentheses past 88 characters. */
function pyString(name, value) {
  const one = `${name} = ${lit(value)}`;
  if (one.length <= 88) return [one];
  const parts = String(value).split(/(?<=\| )|(?=\/releases\/)/);
  return [`${name} = (`, ...parts.map((x) => `    ${lit(x)}`), ")"];
}

/** A per-try cost cap as a Python number: cents above $0.10, else as set. */
function capLiteral(value) {
  const n = Number(value ?? 1);
  if (!Number.isFinite(n) || n <= 0) return "1.00";
  return n >= 0.1 ? n.toFixed(2) : String(n);
}

/** `NAME = "…"`, split at spaces over lines in parentheses past 88 characters. */
function pyWords(name, value) {
  const one = `${name} = ${lit(value)}`;
  if (one.length <= 88) return [one];
  const parts = [];
  let cur = "";
  String(value)
    .split(/(?<= )/)
    .forEach((w) => {
      if (cur && lit(cur + w).length > 80) {
        parts.push(cur);
        cur = w;
      } else cur += w;
    });
  if (cur) parts.push(cur);
  return [`${name} = (`, ...parts.map((x) => `    ${lit(x)}`), ")"];
}

/** `NAME = {"k": [..], ...}` of string lists, one key per line. */
function dictOfLists(name, dict, comment) {
  const L = [`${name} = {${comment ? `  # ${comment}` : ""}`];
  Object.entries(dict).forEach(([k, list]) => {
    if (!list.length) {
      L.push(`    ${lit(k)}: [],`);
      return;
    }
    L.push(`    ${lit(k)}: [`);
    L.push(...listLines(list, 8));
    L.push("    ],");
  });
  L.push("}");
  return L;
}

/** The MCP_SERVERS constant (name → {command, args} or {type, url}). */
function mcpConstant(servers, where, mcpMode) {
  if (!servers.length)
    return ["MCP_SERVERS = {}  # no MCP servers in the recipe"];
  const L = [`MCP_SERVERS = {  # ${where}`];
  servers.forEach((s) => {
    const off = excludedToolsOf(s, mcpMode);
    const offLine = off.length
      ? `"exclude_tools": [${off.map(lit).join(", ")}]`
      : null;
    if (s.url) {
      L.push(
        `    ${lit(s.name)}: {"type": "http", "url": ${lit(s.url)}${offLine ? `, ${offLine}` : ""}},`,
      );
      return;
    }
    const [cmd, ...args] = mcpArgv(s);
    L.push(`    ${lit(s.name)}: {`);
    L.push(`        "command": ${lit(cmd)},`);
    L.push(`        "args": [${args.map(lit).join(", ")}],`);
    if ((s.env ?? []).length) L.push(`        "env_secret": ${lit(s.env[0])},`);
    if (offLine) L.push(`        ${offLine},  # unticked in Choose tools`);
    L.push("    },");
  });
  L.push("}");
  return L;
}

const INSTRUCTIONS_AT_WORDS = {
  "--read": "passed to the CLI with --read",
  task: "put before the task (no instructions file)",
  env: "FI_INSTRUCTIONS_FILE in your command's environment",
};

const MCP_WHERE = {
  claude: "written to .fi/mcp.json; claude gets --mcp-config",
  custom: "written to .fi/mcp.json; FI_MCP_CONFIG points at it",
  codex: "written to .fi/codex/config.toml; CODEX_HOME=.fi/codex",
  gemini: "written to .gemini/settings.json (mcpServers)",
  openhands: "written to .fi/openhands.toml ([mcp] stdio_servers)",
  goose: "one --with-extension per server on the goose command line",
};

/** `write_configs(folder)`: the harness's own config files for one try. */
function writeConfigsPart(mode, id, parser, excludes = false) {
  const L = [
    "def write_configs(folder):",
    '    """The harness\'s own config files in a fresh folder (MCP, tools, budget)."""',
    "    def write(rel, text):",
    "        path = os.path.join(folder, rel)",
    "        os.makedirs(os.path.dirname(path), exist_ok=True)",
    '        with open(path, "w", encoding="utf-8") as f:',
    "            f.write(text)",
    "        if SHOW_CONFIGS:",
    "            print(f\"  wrote {rel}: {' '.join(text.split())[:240]}\")",
    "",
    '    servers = {k: {"command": v.get("command"), "args": v.get("args", [])}',
    '               if "command" in v else v for k, v in MCP_SERVERS.items()}',
  ];
  if (mode === "claude" || mode === "custom") {
    L.push("    if servers:");
    L.push('        body = json.dumps({"mcpServers": servers}, indent=2)');
    L.push('        write(os.path.join(".fi", "mcp.json"), body)');
  }
  if (mode === "codex") {
    L.push("    toml = []");
    L.push("    for name, spec in servers.items():");
    L.push('        toml.append(f"[mcp_servers.{json.dumps(name)}]")');
    L.push('        if "url" in spec:');
    L.push("            toml.append(f\"url = {json.dumps(spec['url'])}\")");
    L.push("        else:");
    L.push(
      "            toml.append(f\"command = {json.dumps(spec['command'])}\")",
    );
    L.push("            toml.append(f\"args = {json.dumps(spec['args'])}\")");
    if (excludes) {
      L.push('        off = MCP_SERVERS[name].get("exclude_tools")');
      L.push("        if off:  # the tools unticked in Choose tools stay off");
      L.push('            toml.append(f"disabled_tools = {json.dumps(off)}")');
    }
    L.push("    if toml:");
    L.push(
      '        write(os.path.join(".fi", "codex", "config.toml"), "\\n".join(toml))',
    );
  }
  if (mode === "gemini") {
    // its turn limit (Max turns): Gemini CLI stops the session at N turns
    L.push('    settings = {"coreTools": CORE_TOOLS}');
    L.push('    if TURNS_KEY == "maxSessionTurns":  # 0.x: a top-level key');
    L.push('        settings["maxSessionTurns"] = MAX_TURNS');
    L.push("    else:  # 1.x: under model");
    L.push('        settings["model"] = {"maxSessionTurns": MAX_TURNS}');
    if (excludes) {
      L.push("    for name, spec in MCP_SERVERS.items():");
      L.push(
        '        if spec.get("exclude_tools"):  # unticked in Choose tools',
      );
      L.push("            servers[name] = {");
      L.push(
        '                **{k: v for k, v in servers[name].items() if k != "exclude_tools"},',
      );
      L.push('                "excludeTools": spec["exclude_tools"],');
      L.push("            }");
    }
    L.push("    if servers:");
    L.push('        settings["mcpServers"] = servers');
    L.push(
      '    write(os.path.join(".gemini", "settings.json"), json.dumps(settings))',
    );
  }
  if (id === "openhands" && parser === "openhands_v0") {
    // runtime local: the agent works in this folder (0.x defaults to docker);
    // the trajectory is its step record; the budget is OpenHands' own cap.
    L.push('    toml = ["[core]", f"max_iterations = {MAX_TURNS}",');
    L.push("            'runtime = \"local\"',");
    L.push('            f"save_trajectory_path = {json.dumps(RECORD_FILE)}",');
    L.push('            f"max_budget_per_task = {MAX_COST_USD}", "",');
    L.push('            "[llm]", f"model = {json.dumps(MODEL)}"]');
    L.push("    stdio = [");
    L.push('        {"name": k, "command": v["command"], "args": v["args"]}');
    L.push('        for k, v in servers.items() if "command" in v');
    L.push("    ]");
    L.push("    if stdio:");
    L.push('        items = ", ".join(');
    L.push('            "{name = %s, command = %s, args = %s}"');
    L.push('            % (json.dumps(s["name"]), json.dumps(s["command"]),');
    L.push('               json.dumps(s["args"]))');
    L.push("            for s in stdio");
    L.push("        )");
    L.push('        toml += ["", "[mcp]", f"stdio_servers = [{items}]"]');
    L.push(
      '    write(os.path.join(".fi", "openhands.toml"), "\\n".join(toml) + "\\n")',
    );
  }
  L.push('    source = os.path.join(folder, INSTRUCTIONS_FILE or "")');
  L.push(
    '    if INSTRUCTIONS_AT and INSTRUCTIONS_AT not in ("--read", "task", "env"):',
  );
  L.push("        if INSTRUCTIONS_FILE and os.path.isfile(source):");
  L.push("            target = os.path.join(folder, INSTRUCTIONS_AT)");
  L.push("            if os.path.abspath(target) != os.path.abspath(source):");
  L.push("                os.makedirs(os.path.dirname(target), exist_ok=True)");
  L.push("                shutil.copyfile(source, target)");
  L.push("                if SHOW_CONFIGS:");
  L.push(
    '                    print(f"  copied {INSTRUCTIONS_FILE} to {INSTRUCTIONS_AT}")',
  );
  return L;
}

/** The argv of each permission level (`{task}` = the row's task). */
function cliArgvByLevel(recipe) {
  const out = {};
  PERMISSION_ORDER.forEach((level) => {
    const r = { ...recipe, permissions: level };
    out[level] = commandPreview(r, { task: "{task}" }).argv;
  });
  return out;
}

/**
 * run_harness_and_upload.py for one harness (complete file).
 * @param {{entry: object, ids: object, recipe?: object}} p
 * @returns {string}
 */
export function harnessScript({ entry, ids, recipe }) {
  const d = defaults(ids);
  const id = entry?.id ?? "custom_command";
  const r = asRecipe(recipe ?? defaultRecipe(entry, null), entry);
  const p = profileOf(id);
  const parser = parserOf(id, r.version);
  const recordFile = RECORD_FILES[parser] ?? null;
  const price = MODEL_PRICES[r.model] ?? null;
  const servers = p.mcp ? r.mcp_servers ?? [] : [];
  const key = recordKeyOf(entry ?? id, { mode: "local", form: "upload" });
  const install = installLine(r);
  const env = harnessEnv({ ...r, permissions: "edit" });
  Object.keys(permissionOf(id, "edit").env).forEach((k) => delete env[k]);
  const permEnv = Object.fromEntries(
    PERMISSION_ORDER.map((l) => [l, permissionOf(id, l).env]),
  );
  const wrapper = permissionOf(id, r.permissions).wrapper;
  const argvBy = Object.fromEntries(
    Object.entries(cliArgvByLevel(r)).map(([l, argv]) => [l, argv.map(lit)]),
  );
  const collect = collectOf(r);
  const tools = Object.entries(r.builtin_tools ?? {})
    .filter(([, on]) => on)
    .map(([t]) => t);
  const name = entry?.name ?? "Custom command";
  const L = [];
  L.push(
    ...commentLines(
      `TRACE + UPLOAD: traces each try with traceAI. ${UPLOAD_LINE}`,
    ),
  );
  L.push(
    ...commentLines(
      `${HARNESS_FILE} · run ${d.runLabel} (${name}) on "${d.datasetName}"`,
    ),
  );
  L.push(`#   python ${HARNESS_FILE} --workdir .`);
  L.push(`#   python ${HARNESS_FILE} --workdir . --limit 1`);
  L.push(`#   python ${HARNESS_FILE} --workdir . --dry-run`);
  if (install) L.push(...commentLines(`Install ${name} first: ${install}`));
  L.push(
    ...commentLines(
      `Each row and try gets a fresh working copy; ${p.bin} runs there with the row's ${d.inputColumn}. --dry-run reads sample_rows.jsonl and prints the upload instead of sending it.`,
    ),
  );
  L.push("import argparse");
  L.push("import concurrent.futures");
  L.push("import glob");
  L.push("import json");
  L.push("import math");
  L.push("import os");
  L.push("import re");
  L.push("import shlex");
  L.push("import shutil");
  L.push("import signal");
  L.push("import stat");
  L.push("import subprocess");
  L.push("import sys");
  L.push("import tempfile");
  L.push("import threading");
  L.push("import time");
  L.push("from datetime import datetime");
  L.push("");
  L.push("import requests");
  L.push("from fi_instrumentation import register");
  L.push("from fi_instrumentation.fi_types import ProjectType");
  L.push("from opentelemetry import baggage, context");
  L.push("");
  L.push(`FI_BASE_URL = os.environ.get("FI_BASE_URL", ${lit(FI_BASE_URL)})`);
  L.push(`DATASET_ID = ${lit(d.datasetId)}`);
  L.push(`EXPERIMENT_ID = ${lit(d.experimentId)}  # reserved for this draft`);
  L.push(`RUN_ID = ${lit(d.runId)}  # run ${d.runLabel}`);
  L.push(`RUN_LABEL = ${lit(d.runLabel)}`);
  L.push(`PROJECT_NAME = ${lit(`${d.datasetName} · runs`)}`);
  L.push(`INPUT_COLUMN = ${lit(d.inputColumn)}  # the task the CLI gets`);
  L.push(`TRIES = ${d.tries}  # tries per row`);
  L.push(`HARNESS = ${lit(name)}`);
  L.push(`HARNESS_VERSION = ${lit(r.version || "")}`);
  L.push(...pyString("INSTALL", install ?? ""));
  L.push(...pyWords("CAPTURE", RECORD_WORDS[key]));
  L.push(`PARSER = ${lit(parser)}  # how section 4 reads the CLI's record`);
  L.push(
    `RECORD_FILE = ${recordFile ? lit(recordFile) : "None"}  # ${recordFile ? "written by the CLI in the try folder" : "the CLI's stdout stream"}`,
  );
  L.push(`MODEL = ${lit(r.model)}`);
  L.push(
    `HELPER_MODEL = ${p.subCall && r.subCallModel ? lit(r.subCallModel) : "None"}  # ${p.subCall ? p.subCall.env : "no helper-model setting"}`,
  );
  L.push(`PERMISSIONS = ${lit(r.permissions)}  # --permissions overrides it`);
  L.push(
    `WRAPPER_ENFORCED = ${wrapper ? "True" : "False"}  # ${wrapper ? "the wrapper applies the level" : "the CLI applies the level"}`,
  );
  L.push(`MAX_TURNS = ${Number(r.budget?.max_turns) || 40}`);
  L.push(`TIMEOUT_S = ${Number(r.budget?.timeout_s) || 600}  # per try`);
  L.push(
    `MAX_COST_USD = ${capLiteral(r.budget?.max_cost_usd)}  # per try, checked against the try's cost`,
  );
  L.push(
    `PRICE_PER_1M = {${price ? `${lit(r.model)}: (${price[0]}, ${price[1]})` : ""}}  # gateway list price in/out`,
  );
  if (TOKEN_PRICED.includes(parser))
    L.push(
      "# cost = tokens × PRICE_PER_1M when the CLI reports tokens, not dollars",
    );
  L.push(
    "GRACE_S = 10  # after SIGINT at the time limit, then the CLI is killed",
  );
  L.push(...mcpConstant(servers, MCP_WHERE[p.mcp] ?? "", p.mcp));
  L.push(`ALLOWED_TOOLS = [${tools.map(lit).join(", ")}]  # built-in tools`);
  if (id === "claude_code") {
    const t = claudeToolLists(r);
    L.push(`# claude: --allowedTools ${t.allowed.join(",") || "(none)"}`);
  }
  if (p.mcp === "gemini") {
    L.push(
      ...["CORE_TOOLS = [", ...listLines(geminiCoreTools(r).map(lit), 4), "]"],
    );
    L.push(
      `TURNS_KEY = ${lit(geminiTurnsKey(r))}  # where settings.json takes MAX_TURNS`,
    );
  }
  L.push(
    `INSTRUCTIONS_FILE = ${r.instructions ? lit(r.instructions) : "None"}`,
  );
  L.push(
    `INSTRUCTIONS_AT = ${lit(p.instructionsAt ?? "")}  # ${INSTRUCTIONS_AT_WORDS[p.instructionsAt] ?? `where ${name} reads it`}`,
  );
  L.push(`COLLECT = [${collect.map(lit).join(", ")}]  # only these upload`);
  L.push(`EXTRA_FILES = [${(r.extra_files ?? []).map(lit).join(", ")}]`);
  L.push("HARNESS_ENV = {");
  Object.entries(env).forEach(([k, v]) => L.push(`    ${lit(k)}: ${lit(v)},`));
  L.push("}");
  L.push("PERMISSION_ENV = {");
  Object.entries(permEnv).forEach(([l, e]) =>
    L.push(
      `    ${lit(l)}: {${Object.entries(e)
        .map(([k, v]) => `${lit(k)}: ${lit(v)}`)
        .join(", ")}},`,
    ),
  );
  L.push("}");
  L.push(...dictOfLists("CLI_ARGV", argvBy, "per permission level"));
  L.push("HEADERS = {");
  L.push('    "X-Api-Key": os.environ.get("FI_API_KEY", ""),');
  L.push('    "X-Secret-Key": os.environ.get("FI_SECRET_KEY", ""),');
  L.push("}");
  L.push("HERE = os.path.dirname(os.path.abspath(__file__))");
  L.push("SHOW_CONFIGS = False  # --dry-run prints the config files it writes");
  L.push("");
  L.push("");
  L.push("# 1. fetch_rows(): the dataset's rows");
  L.push("def fetch_rows():");
  L.push(
    '    """This dataset\'s rows as {"id": row id, <column name>: value}."""',
  );
  L.push(
    '    url = f"{FI_BASE_URL}/model-hub/develops/{DATASET_ID}/get-dataset-table/"',
  );
  L.push("    page, total_pages = 0, 1");
  L.push("    while page < total_pages:");
  L.push('        params = {"page_size": 100, "current_page_index": page}');
  L.push(
    "        resp = requests.get(url, headers=HEADERS, params=params, timeout=60)",
  );
  L.push("        resp.raise_for_status()");
  L.push('        result = resp.json()["result"]');
  L.push(
    '        names = {c["id"]: c["name"] for c in result["columnConfig"]}',
  );
  L.push('        for row in result["table"]:');
  L.push('            item = {"id": row["rowId"]}');
  L.push("            for col_id, name in names.items():");
  L.push(
    '                item[name] = (row.get(col_id) or {}).get("cellValue")',
  );
  L.push("            yield item");
  L.push('        total_pages = result["metadata"]["totalPages"]');
  L.push("        page += 1");
  L.push("");
  L.push("");
  L.push('def read_sample_rows(path="sample_rows.jsonl"):');
  L.push(
    '    """--dry-run: rows from a local file, one JSON object per line."""',
  );
  L.push('    with open(path, encoding="utf-8") as f:');
  L.push("        return [json.loads(line) for line in f if line.strip()]");
  L.push("");
  L.push("");
  L.push("def pick_rows(rows, which, dry_run):");
  L.push(
    '    """--rows all | failed | new | <id,id,…> (failed/new read the run)."""',
  );
  L.push('    if which in (None, "all"):');
  L.push("        return rows");
  L.push('    if which in ("failed", "new"):');
  L.push(
    '        url = f"{FI_BASE_URL}/model-hub/experiments/v2/{EXPERIMENT_ID}"',
  );
  L.push('        url += f"/runs/{RUN_ID}/results/"');
  L.push("        if dry_run:");
  L.push(
    '            print(f"GET {url}  # the run\'s results, for --rows {which}")',
  );
  L.push("            return rows");
  L.push(
    "        done = requests.get(url, headers=HEADERS, timeout=60).json()",
  );
  L.push(
    '        seen = {r["example_id"]: r for r in done.get("results", [])}',
  );
  L.push('        if which == "new":');
  L.push('            return [r for r in rows if r["id"] not in seen]');
  L.push(
    '        bad = {k for k, v in seen.items() if v.get("error") or not v.get("passed")}',
  );
  L.push('        return [r for r in rows if r["id"] in bad]');
  L.push('    wanted = set(which.split(","))');
  L.push('    return [r for r in rows if r["id"] in wanted]');
  L.push("");
  L.push("");
  L.push("# 2. fresh_folder(row, trial): a working copy per try");
  L.push(
    ...writeConfigsPart(
      p.mcp,
      id,
      parser,
      servers.some((sv) => excludedToolsOf(sv, p.mcp).length > 0),
    ),
  );
  L.push("");
  L.push("");
  L.push("def set_writable(folder, writable):");
  L.push(
    '    """Read only (wrapper): the copy is locked, so writes fail and are discarded."""',
  );
  L.push("    paths = [folder]");
  L.push("    for root, dirs, files in os.walk(folder):");
  L.push(
    "        paths += [os.path.join(root, name) for name in dirs + files]",
  );
  L.push("    bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH");
  L.push("    for path in paths:");
  L.push("        mode = os.stat(path).st_mode");
  L.push(
    "        os.chmod(path, mode | stat.S_IWUSR if writable else mode & ~bits)",
  );
  L.push("");
  L.push("");
  L.push("def fresh_folder(row, trial, workdir, permissions):");
  L.push(
    '    """A fresh working copy for one try: a git worktree of the task\'s repo',
  );
  L.push(
    '    at start_state["commit"] when the row has one, else a copy of --workdir."""',
  );
  L.push('    start = row.get("start_state")');
  L.push("    if isinstance(start, str):");
  L.push("        try:");
  L.push("            start = json.loads(start)");
  L.push("        except ValueError:");
  L.push("            start = None");
  L.push("    start = start if isinstance(start, dict) else {}");
  L.push('    folder = tempfile.mkdtemp(prefix=f"fi-try-{trial}-")');
  L.push('    if start.get("repo") and start.get("commit"):');
  L.push("        os.rmdir(folder)");
  L.push(
    '        git = ["git", "-C", start["repo"], "worktree", "add", "--detach"]',
  );
  L.push('        subprocess.run([*git, folder, start["commit"]], check=True)');
  L.push("    else:");
  L.push(
    '        skip = shutil.ignore_patterns(".git", ".venv*", "__pycache__")',
  );
  L.push(
    "        shutil.copytree(workdir, folder, ignore=skip, dirs_exist_ok=True)",
  );
  L.push(
    "        write_configs(folder)  # part of the baseline, so not in the diff",
  );
  L.push(
    '        if shutil.which("git"):  # a baseline commit, so the diff is the try\'s',
  );
  L.push('            quiet = {"cwd": folder, "capture_output": True}');
  L.push('            subprocess.run(["git", "init", "-q"], **quiet)');
  L.push('            subprocess.run(["git", "add", "-A"], **quiet)');
  L.push(
    '            who = ["-c", "user.name=fi", "-c", "user.email=fi@localhost"]',
  );
  L.push('            commit = ["commit", "-qm", "start", "--allow-empty"]');
  L.push('            subprocess.run(["git", *who, *commit], **quiet)');
  L.push('    if start.get("repo") and start.get("commit"):');
  L.push("        write_configs(folder)");
  L.push('    if WRAPPER_ENFORCED and permissions == "read_only":');
  L.push("        set_writable(folder, False)");
  L.push("    return folder");
  L.push("");
  L.push("");
  L.push(
    `# 3. run_cli(task, folder): the CLI headless (flags from the ${name} docs)`,
  );
  L.push("def cli_command(task, permissions, dry_run):");
  L.push(
    '    """The headless command for one try at this permission level."""',
  );
  L.push(
    '    return [task if a == "{task}" else a for a in CLI_ARGV[permissions]]',
  );
  L.push("");
  L.push("");
  if (id === "codex_cli") {
    L.push("def codex_step(line):");
    L.push(
      '    """One agent step of codex exec --json: a finished command, MCP call,',
    );
    L.push('    edit or web search (Max turns counts these)."""');
    L.push("    try:");
    L.push("        event = json.loads(line)");
    L.push("    except ValueError:");
    L.push("        return False");
    L.push('    item = event.get("item") or {}');
    L.push(
      '    return event.get("type") == "item.completed" and item.get("type") in (',
    );
    L.push(
      '        "command_execution", "mcp_tool_call", "file_change", "web_search")',
    );
    L.push("");
    L.push("");
  }
  L.push(
    "def run_cli(task, folder, permissions, params, dry_run, traceparent=None):",
  );
  L.push(
    '    """Run the CLI in its folder: ([(arrival ns, stdout line)], error or None).',
  );
  L.push(
    "    Each line is stamped as it arrives. At TIMEOUT_S the CLI gets SIGINT (mini",
  );
  L.push(
    "    and OpenHands save their record on it), GRACE_S more, then a kill; the",
  );
  L.push('    lines read so far are kept and the try is marked timed out."""');
  L.push("    env = {**os.environ, **PERMISSION_ENV[permissions]}");
  L.push(
    "    for key, value in HARNESS_ENV.items():  # .fi/… paths live in the folder",
  );
  L.push('        local = value.startswith(".fi/")');
  L.push("        env[key] = os.path.join(folder, value) if local else value");
  L.push('    env["FI_PERMISSIONS"] = permissions');
  L.push('    env["FI_MAX_TURNS"] = str(MAX_TURNS)');
  if (id === "openhands" && isOpenHandsV1(r))
    L.push(
      '    env["MAX_ITERATIONS"] = str(MAX_TURNS)  # read with --override-with-envs',
    );
  L.push(
    "    if traceparent:  # W3C context: the CLI's own OTel spans nest here",
  );
  L.push('        env["TRACEPARENT"] = traceparent');
  L.push("    if HELPER_MODEL:");
  L.push(
    `        env[${lit(p.subCall?.env ?? "FI_HELPER_MODEL")}] = HELPER_MODEL`,
  );
  L.push("    for key, value in params.items():");
  L.push('        env[f"FI_PARAM_{key.upper()}"] = value');
  L.push('    os.makedirs(os.path.join(folder, ".fi"), exist_ok=True)');
  L.push("    if dry_run:  # the command the real CLI would get");
  L.push(
    '        print(f"  would run: {shlex.join(cli_command(task, permissions, False))}")',
  );
  L.push("    try:");
  L.push("        proc = subprocess.Popen(");
  L.push("            cli_command(task, permissions, dry_run),");
  L.push("            cwd=folder,");
  L.push("            env=env,");
  L.push("            stdin=subprocess.DEVNULL,");
  L.push("            stdout=subprocess.PIPE,");
  L.push("            stderr=subprocess.PIPE,");
  L.push("            text=True,");
  L.push("            bufsize=1,");
  L.push("        )");
  L.push("    except FileNotFoundError as exc:");
  L.push('        return [], f"CLI not found: {exc.filename}"');
  L.push(
    id === "codex_cli"
      ? "    lines, errs, turns = [], [], []"
      : "    lines, errs = [], []",
  );
  L.push("");
  L.push("    def read_stdout():");
  L.push("        for line in proc.stdout:");
  L.push('            lines.append((time.time_ns(), line.rstrip("\\n")))');
  if (id === "codex_cli") {
    // codex has no turn flag: stop it at Max turns from its own events
    L.push("            if codex_step(line):");
    L.push("                turns.append(1)");
    L.push(
      "                if len(turns) >= MAX_TURNS and proc.poll() is None:",
    );
    L.push("                    proc.send_signal(signal.SIGINT)");
  }
  L.push("");
  L.push("    def read_stderr():");
  L.push("        for line in proc.stderr:");
  L.push("            errs.append(line)");
  L.push("            del errs[:-40]");
  L.push("");
  L.push("    readers = [threading.Thread(target=read_stdout, daemon=True),");
  L.push("               threading.Thread(target=read_stderr, daemon=True)]");
  L.push("    for reader in readers:");
  L.push("        reader.start()");
  L.push("    error = None");
  L.push("    try:");
  L.push("        proc.wait(timeout=TIMEOUT_S)");
  L.push("    except subprocess.TimeoutExpired:");
  L.push("        proc.send_signal(signal.SIGINT)");
  L.push("        try:");
  L.push("            proc.wait(timeout=GRACE_S)");
  L.push("        except subprocess.TimeoutExpired:");
  L.push("            proc.kill()");
  L.push("            proc.wait()");
  L.push('        error = f"timed out after {TIMEOUT_S} s"');
  L.push("    for reader in readers:");
  L.push("        reader.join(timeout=5)");
  if (id === "codex_cli") {
    L.push("    if error is None and len(turns) >= MAX_TURNS:");
    L.push('        error = f"stopped at {MAX_TURNS} turns (Max turns)"');
  }
  L.push("    if error is None and proc.returncode != 0:");
  L.push(
    "        error = f\"exit {proc.returncode}: {''.join(errs).strip()[-300:]}\"",
  );
  L.push("    return lines, error");
  L.push("");
  L.push("");
  L.push(...commentLines(`4. Spans with the baton: ${RECORD_WORDS[key]}`));
  L.push(...parserLines(parser));
  L.push("");
  L.push("");
  L.push("# 5. collect(folder): only the artifacts in COLLECT");
  L.push("def collect(folder, lines, final, permissions):");
  L.push(
    '    """The try\'s artifacts: git diff (without .fi/), final message, log, files."""',
  );
  L.push("    out = {}");
  L.push('    locked = WRAPPER_ENFORCED and permissions == "read_only"');
  L.push(
    '    if "git_diff" in COLLECT and shutil.which("git") and not locked:',
  );
  L.push(
    '        quiet = {"cwd": folder, "capture_output": True, "text": True}',
  );
  L.push(
    '        skip = [":!.fi", ":!.aider*"]  # the record files, not the try\'s work',
  );
  L.push(
    '        subprocess.run(["git", "add", "-A", "--", ".", *skip], **quiet)',
  );
  L.push('        diff = subprocess.run(["git", "diff", "--cached"], **quiet)');
  L.push('        out["git_diff"] = diff.stdout or None');
  L.push('    elif "git_diff" in COLLECT:');
  L.push('        out["git_diff"] = None  # read only: writes were discarded');
  L.push('    if "final_message" in COLLECT:');
  L.push('        out["final_message"] = final');
  L.push('    if "log" in COLLECT:');
  L.push('        out["log"] = "\\n".join(line for _, line in lines)[-20000:]');
  L.push('    if "files" in COLLECT:');
  L.push("        found = {}");
  L.push("        for pattern in EXTRA_FILES:");
  L.push("            for path in glob.glob(os.path.join(folder, pattern)):");
  L.push(
    '                with open(path, encoding="utf-8", errors="replace") as f:',
  );
  L.push(
    "                    found[os.path.relpath(path, folder)] = f.read()[:20000]",
  );
  L.push('        out["files"] = found');
  L.push("    return out");
  L.push("");
  L.push("");
  L.push("def run_try(tracer, row, trial, args, params):");
  L.push(
    '    """One row and try in one span with the baton fi.run_id / fi.row_id / fi.trial."""',
  );
  L.push('    task = str(row.get(INPUT_COLUMN) or "")');
  L.push('    if INSTRUCTIONS_AT == "task" and INSTRUCTIONS_FILE:');
  L.push("        path = os.path.join(args.workdir, INSTRUCTIONS_FILE)");
  L.push("        if os.path.isfile(path):");
  L.push('            with open(path, encoding="utf-8") as f:');
  L.push('                task = f.read().strip() + "\\n\\n" + task');
  L.push('    ctx = baggage.set_baggage("fi.run_id", args.run)');
  L.push('    ctx = baggage.set_baggage("fi.row_id", row["id"], context=ctx)');
  L.push('    ctx = baggage.set_baggage("fi.trial", str(trial), context=ctx)');
  L.push("    token = context.attach(ctx)");
  L.push(
    '    attrs = {"fi.run_id": args.run, "fi.row_id": row["id"], "fi.trial": trial}',
  );
  L.push(
    '    attrs.update({"fi.harness": f"{HARNESS} {HARNESS_VERSION}".strip()})',
  );
  L.push(
    '    attrs.update({"fi.capture": CAPTURE, "fi.permissions": args.permissions})',
  );
  L.push("    started = time.time()");
  L.push(
    "    folder = fresh_folder(row, trial, args.workdir, args.permissions)",
  );
  L.push(
    '    with tracer.start_as_current_span("row", attributes=attrs) as span:',
  );
  L.push("        ids = span.get_span_context()");
  L.push('        trace_id = format(ids.trace_id, "032x")');
  L.push('        traceparent = f"00-{trace_id}-{ids.span_id:016x}-01"');
  L.push("        lines, error = run_cli(");
  L.push(
    "            task, folder, args.permissions, params, args.dry_run, traceparent",
  );
  L.push("        )");
  L.push("        result = parse_try(lines, folder)  # before the folder goes");
  L.push('        emit(tracer, result["steps"])');
  L.push('        if result["cost_usd"] is not None:');
  L.push('            span.set_attribute("fi.cost_usd", result["cost_usd"])');
  L.push("    context.detach(token)");
  L.push('    final, cost = result["final"], result["cost_usd"]');
  L.push('    error = error or result["error"]');
  L.push("    if args.dry_run:");
  L.push('        print(f"  steps, parsed ({PARSER}):")');
  L.push('        show(result["steps"])');
  L.push("        print(f\"  final: {(final or '')[:72]!r}\")");
  L.push('        spent = "—" if cost is None else usd(cost)');
  L.push("        print(f\"  cost: {spent} · tokens {result['tokens']}\")");
  L.push("    artifacts = collect(folder, lines, final, args.permissions)");
  L.push("    set_writable(folder, True)");
  L.push("    shutil.rmtree(folder, ignore_errors=True)");
  L.push("    if cost is not None and cost > MAX_COST_USD:");
  L.push(
    '        error = error or f"stopped at the cost cap {usd(MAX_COST_USD)} per try"',
  );
  L.push(`    print(f"{row['id']} try {trial}: {'error' if error else 'ok'}")`);
  L.push("    return {");
  L.push('        "example_id": row["id"],');
  L.push('        "trial": trial,');
  L.push('        "output": final,');
  L.push('        "error": error,');
  L.push('        "latency_ms": int((time.time() - started) * 1000),');
  L.push('        "trace_id": trace_id,');
  L.push('        "cost_usd": cost,');
  L.push('        "artifacts": artifacts,');
  L.push("    }");
  L.push("");
  L.push("");
  L.push(
    "# 6. upload(results): POST …/runs/{run_id}/results/ (see the header)",
  );
  L.push("def upload(results, run_id, dry_run=False):");
  L.push(
    '    """POST in batches of 50; the last batch marks the run complete."""',
  );
  L.push("    url = (");
  L.push('        f"{FI_BASE_URL}/model-hub/experiments/v2/{EXPERIMENT_ID}"');
  L.push('        f"/runs/{run_id}/results/"');
  L.push("    )");
  L.push(
    "    batches = [results[i : i + 50] for i in range(0, len(results), 50)] or [[]]",
  );
  L.push("    for n, batch in enumerate(batches, start=1):");
  L.push('        body = {"results": batch, "complete": n == len(batches)}');
  L.push("        if dry_run:");
  L.push('            print(f"POST {url}")');
  L.push("            print(json.dumps(body, indent=2))");
  L.push("            continue");
  L.push(
    "        resp = requests.post(url, headers=HEADERS, json=body, timeout=60)",
  );
  L.push("        resp.raise_for_status()");
  L.push('        print(f"uploaded batch {n} of {len(batches)}")');
  L.push("");
  L.push("");
  L.push("def main():");
  L.push("    parser = argparse.ArgumentParser(");
  L.push(
    `        description=${lit(`Run ${d.runLabel} (${name}) and upload it.`)}`,
  );
  L.push("    )");
  L.push("    add = parser.add_argument");
  L.push(
    '    add("--workdir", default=".", help="the folder each try copies")',
  );
  L.push('    add("--run", default=RUN_ID, help="the run id to upload to")');
  L.push(
    '    add("--rows", default="all", help="all | failed | new | <id,id,…>")',
  );
  L.push('    add("--limit", type=int, default=None, help="first N rows")');
  L.push('    add("--trials", type=int, default=TRIES, help="tries per row")');
  L.push(
    '    add("--concurrency", type=int, default=1, help="tries at a time")',
  );
  L.push(
    '    add("--max-cost-usd", type=float, default=None, help="stop the run at $X")',
  );
  L.push(
    '    add("--timeout-s", type=int, default=None, help="seconds per try")',
  );
  L.push(
    '    add("--param", action="append", default=[], help="key=value for the CLI")',
  );
  L.push(
    '    add("--permissions", default=PERMISSIONS, choices=list(CLI_ARGV))',
  );
  L.push(
    '    add("--fail-under", type=float, default=None, help="exit 1 below this pass rate")',
  );
  L.push(
    '    add("--dry-run", action="store_true", help="no upload: print what would be sent")',
  );
  L.push("    args = parser.parse_args()");
  L.push("    global TIMEOUT_S, SHOW_CONFIGS");
  L.push("    TIMEOUT_S = args.timeout_s or TIMEOUT_S");
  L.push("    SHOW_CONFIGS = args.dry_run");
  L.push('    params = dict(p.split("=", 1) for p in args.param if "=" in p)');
  L.push(
    '    if not args.dry_run and not (HEADERS["X-Api-Key"] and HEADERS["X-Secret-Key"]):',
  );
  L.push(
    '        sys.exit("Set FI_API_KEY and FI_SECRET_KEY in this shell first.")',
  );
  L.push("    trace_provider = register(");
  L.push("        project_type=ProjectType.EXPERIMENT,");
  L.push("        project_name=PROJECT_NAME,");
  L.push("        project_version_name=RUN_LABEL,");
  L.push("    )");
  L.push('    tracer = trace_provider.get_tracer("run_harness_and_upload")');
  L.push(
    "    rows = read_sample_rows() if args.dry_run else list(fetch_rows())",
  );
  L.push("    rows = pick_rows(rows, args.rows, args.dry_run)[: args.limit]");
  L.push('    who = f"{HARNESS} · {MODEL} · {args.permissions}"');
  L.push('    print(f"{who} · {len(rows)} rows × {args.trials} tries")');
  L.push(
    "    jobs = [(row, t) for row in rows for t in range(1, args.trials + 1)]",
  );
  L.push("    results, spent = [], 0.0");
  L.push(
    "    with concurrent.futures.ThreadPoolExecutor(args.concurrency) as pool:",
  );
  L.push("        for i in range(0, len(jobs), args.concurrency):");
  L.push(
    "            if args.max_cost_usd is not None and spent >= args.max_cost_usd:",
  );
  L.push(
    '                print(f"stopped at the cost cap {usd(args.max_cost_usd)}")',
  );
  L.push("                break");
  L.push("            batch = jobs[i : i + args.concurrency]");
  L.push("            futures = [");
  L.push("                pool.submit(run_try, tracer, row, t, args, params)");
  L.push("                for row, t in batch");
  L.push("            ]");
  L.push("            for f in futures:");
  L.push("                results.append(f.result())");
  L.push('                spent += results[-1]["cost_usd"] or 0.0');
  L.push("    trace_provider.force_flush()");
  L.push("    upload(results, args.run, dry_run=args.dry_run)");
  // the CI gate (ci.js prints --fail-under): tries that ended without an error
  L.push('    ok = [r for r in results if not r["error"]]');
  L.push("    rate = len(ok) / len(results) if results else 0.0");
  L.push('    print(f"pass rate {rate:.2f}")');
  L.push("    if args.fail_under is not None and rate < args.fail_under:");
  L.push(
    '        sys.exit(f"pass rate {rate:.2f} is below --fail-under {args.fail_under}")',
  );
  L.push("");
  L.push("");
  L.push('if __name__ == "__main__":');
  L.push("    main()");
  return `${L.join("\n")}\n`;
}

/** The `Trace + upload` steps of a harness (complete file + how to run it). */
function uploadSteps({ entry, ids, recipe }) {
  const r = asRecipe(recipe ?? defaultRecipe(entry, null), entry);
  const file = harnessScript({ entry, ids, recipe: r });
  const install = installLine(r);
  return [
    {
      n: 1,
      id: "install",
      title: "Install",
      lang: "bash",
      code: [`pip install ${HARNESS_PACKAGES}`, install]
        .filter(Boolean)
        .join("\n"),
      note: install
        ? `${entry?.name ?? "the CLI"} pinned to ${r.version}`
        : "plus your CLI itself",
    },
    keysStep(2, ids),
    {
      n: 3,
      id: "file",
      title: HARNESS_FILE,
      lang: "python",
      code: file,
      fileName: HARNESS_FILE,
    },
    {
      n: 4,
      id: "run",
      title: "Run it",
      lang: "bash",
      code: harnessTryCommand(ids),
      note: "one row first; drop --limit 1 to run them all",
    },
  ];
}

/**
 * The frozen slot signature. `ids` may carry `recipe` (the recipe the
 * drawer is editing, or a run's C4 config), so the script follows it.
 */
export function steps({ entry, ids = {} } = {}) {
  const e = entry ?? catalogEntry("claude_code");
  return uploadSteps({ entry: e, ids, recipe: ids.recipe });
}
