// IT-R1 G1 P4: `Start it` — how to start every run that executes on the
// user's machine, composed ONLY from each run's own snippet (the same
// builders the drawer uses; no CLI of ours is invented). Pure.
//
//   runStartLine({run, kind, entry, tries, concurrency, maxCostUsd,
//     timeoutS, params, rows, sideEffects, noSandbox}) → string   (I6)
//   runStartCommand({label, kind, entryId, lang, ids, …}) →
//     {label, command, scriptName, script, install, folder, cli, keys}
//   startItPlan({ordinal, runs, sandbox}) →
//     {runs[], shell, shellName, ci, ciName, secrets}
//
// Each run's script lives in its own folder (`run-4A/`), so two harness
// runs (Claude Code, Codex CLI) never overwrite one another's file.
import { catalogEntry } from "../catalog";
import { snippetFor } from "./index";
import {
  HARNESS_FILE,
  HARNESS_PACKAGES,
  harnessScript,
} from "./prototypeHarness";
import { CI_PATH, runnerCommand } from "./lines";
import { SANDBOX_COMMAND, SANDBOX_PORT } from "./sandbox";
import { ciSecrets } from "./ci";
import { defaultRecipe, installLine } from "../recipe/recipeModel";

const slug = (label) =>
  `run-${String(label ?? "")
    .replace(/^#/, "")
    .replace(/[^A-Za-z0-9]+/g, "")}`;

const isTsOnly = (e) =>
  (e?.languages ?? []).includes("typescript") &&
  !(e?.languages ?? []).includes("python");

/** A harness run's recipe (its own, else the harness's default). */
const harnessRecipeOf = (entry, recipe) =>
  recipe && Object.keys(recipe).length
    ? recipe
    : entry?.kind === "harness"
      ? defaultRecipe(entry)
      : null;

/**
 * I6 (S3): THE line that starts one run from a terminal — a code run's
 * `python run_and_upload.py --agent <spec> --run <uuid> …` (or `npx tsx
 * run_and_upload.ts …`), a harness run's `python run_harness_and_upload.py
 * --workdir . --run <uuid> …` — with every limit the run set: tries,
 * at a time, cost cap, time limit, changed params, picked rows (labels),
 * side effects and a skipped sandbox. The waiting panel, banner, tray,
 * run-from-code panel, rerun dialog and Review's Start it print this.
 * @returns {string}
 */
export function runStartLine({
  run,
  kind,
  entry,
  entryId,
  lang,
  tries,
  concurrency,
  maxCostUsd,
  timeoutS,
  limits,
  params,
  rows,
  sideEffects,
  noSandbox = false,
} = {}) {
  const cfg = run?.config ?? {};
  const runId = run?.run_id ?? run?.id ?? undefined;
  const l = limits ?? {};
  const lim = {
    concurrency: concurrency ?? l.concurrency ?? null,
    max_cost_usd: maxCostUsd ?? l.max_cost_usd ?? l.maxCostUsd ?? null,
    timeout_s: timeoutS ?? l.timeout_s ?? l.timeoutS ?? null,
  };
  const trials = tries ?? l.tries;
  const rowList = rows?.length ? [...rows] : undefined;
  const id =
    entry?.id ?? entryId ?? cfg.harness?.id ?? cfg.framework?.id ?? null;
  const e = entry ?? (id ? catalogEntry(id) : null);
  const k = kind ?? cfg.kind ?? run?.kind ?? e?.kind ?? "code";
  if (k === "harness")
    return runnerCommand({
      lang: "harness",
      runId,
      rows: rowList,
      trials,
      limits: lim,
      params,
      sideEffects,
      noSandbox,
    });
  const language =
    lang ?? cfg.framework?.language ?? (isTsOnly(e) ? "typescript" : "python");
  const s = snippetFor({
    kind: "code",
    entryId: e?.id ?? id,
    form: "upload",
    lang: language,
    ids: { runId, sideEffects },
  });
  const o = {
    runId,
    trials,
    limits: lim,
    params,
    rows: rowList,
    noSandbox: Boolean(noSandbox),
  };
  let line = s.command
    ? s.command(o)
    : runnerCommand({
        lang: language === "typescript" ? "typescript" : "python",
        entry: e?.agent?.spec ?? "agent.py:target",
        sideEffects,
        ...o,
      });
  // one agent-spec source: the run's own `config.code.entry`
  const spec = cfg.code?.entry;
  const dflt = /--agent (\S+)/.exec(line)?.[1];
  if (spec && dflt && spec !== dflt)
    line = line.replace(`--agent ${dflt}`, `--agent ${spec}`);
  return line;
}

/**
 * One run's start entry for Start it (the run's folder, script, install,
 * CLI and keys); its `command` is `runStartLine`. Called I6-style (`run`
 * given, no `label` / `useFolder`), it returns the line itself.
 */
export function runStartCommand(args = {}) {
  if (args.run && !("label" in args) && !("useFolder" in args))
    return runStartLine(args);
  const {
    label,
    name,
    kind,
    entryId,
    lang,
    ids = {},
    recipe,
    useFolder = true,
    limits,
    flags,
    models = [],
  } = args;
  const entry = catalogEntry(entryId);
  // One run: the drawer's own command, as is. Several: each script in its
  // run's folder so two of them never overwrite one file.
  const folder = useFolder ? slug(label) : ".";
  const at = (file) => (useFolder ? `${folder}/${file}` : file);
  const f = flags ?? {};
  const runId = ids.runId ?? f.runId;
  const line = (k) =>
    runStartLine({
      run: runId ? { run_id: runId } : undefined,
      kind: k,
      entry,
      lang,
      tries: ids.tries ?? f.trials,
      limits: limits ?? f.limits,
      params: f.params,
      rows: f.rows,
      sideEffects: ids.sideEffects,
      noSandbox: Boolean(f.noSandbox && k === "code" && ids.sandbox),
    });
  if (kind === "harness") {
    const r = harnessRecipeOf(entry, recipe);
    const script = harnessScript({
      entry,
      ids: { ...ids, runLabel: label },
      recipe: r ?? undefined,
    });
    return {
      label,
      name,
      kind,
      entryId,
      folder,
      scriptName: HARNESS_FILE,
      script,
      command: line("harness").replace(HARNESS_FILE, at(HARNESS_FILE)),
      install: `pip install ${HARNESS_PACKAGES}`,
      // the CLI at the recipe's pinned version (npm, pip or Goose's script)
      cli: r ? installLine(r) : null,
      keys: ciSecrets({ entry, recipe: r ?? undefined }).slice(2),
      lang: "python",
    };
  }
  const s = snippetFor({
    kind: "code",
    entryId,
    form: "upload",
    lang,
    ids: { ...ids, runLabel: label },
  });
  const command = line("code").replace(s.fileName, at(s.fileName));
  const install = String(
    s.steps?.[0]?.copyText ?? s.steps?.[0]?.code ?? "",
  ).split("\n")[0];
  const agent = (s.steps ?? []).find((x) => x.id === "agent");
  return {
    label,
    name,
    kind: "code",
    entryId,
    folder,
    scriptName: s.fileName,
    script: s.file,
    agentFile: agent?.fileName ?? null,
    command,
    install,
    cli: null,
    // every model key the run calls (its models and a changed model param)
    keys: ciSecrets({ entry, models, params: f.params }).slice(2),
    lang: s.fileName?.endsWith(".ts") ? "typescript" : "python",
  };
}

const uniq = (xs) => [...new Set(xs.filter(Boolean))];

/**
 * The whole experiment: per-run commands, `run-experiment-4.sh` (starts
 * them with `&` and `wait`) and a GitHub Action that runs the same file.
 * @param {{ordinal: number|null, runs: object[], sandbox?: object|null}} p
 */
export function startItPlan({ ordinal, runs = [], sandbox = null } = {}) {
  const useFolder = runs.length > 1;
  const list = runs.map((r) => runStartCommand({ ...r, useFolder }));
  const n = ordinal ?? "next";
  const shellName = `run-experiment-${n}.sh`;
  const ciName = CI_PATH;
  const width = Math.max(0, ...list.map((r) => r.command.length));
  const shell = [
    "#!/usr/bin/env bash",
    `# ${shellName} · starts the ${list.length} ${list.length === 1 ? "run" : "runs"} of Experiment #${n} on this machine.`,
    useFolder
      ? "# Save each run's script (Copy complete script on its card) in its folder:"
      : "# Save the run's script (Copy complete script on its card) next to this file:",
    ...list.map(
      (r) =>
        `#   ${useFolder ? `${r.folder}/` : ""}${r.scriptName}  (${r.label} ${r.name})`,
    ),
    "set -euo pipefail",
    ...(sandbox
      ? [
          `: "\${${sandbox.envVar}:?start ${sandbox.name} first: ${SANDBOX_COMMAND} &}"`,
        ]
      : []),
    ...list.map((r) => `${r.command.padEnd(width)} &  # ${r.label} ${r.name}`),
    "wait",
    `echo "All ${list.length} runs finished. Results: Experiment #${n} in Future AGI."`,
    "",
  ].join("\n");
  const py = list.some((r) => r.lang === "python");
  const ts = list.some((r) => r.lang === "typescript");
  const clis = uniq(list.map((r) => r.cli));
  const keys = uniq(list.flatMap((r) => r.keys ?? []));
  const ci = [
    `# ${ciName} · runs Experiment #${n} from CI with the same script`,
    "name: fi-experiment",
    "on: workflow_dispatch",
    "jobs:",
    "  run:",
    "    runs-on: ubuntu-latest",
    "    timeout-minutes: 90",
    "    env:",
    "      FI_API_KEY: ${{ secrets.FI_API_KEY }}",
    "      FI_SECRET_KEY: ${{ secrets.FI_SECRET_KEY }}",
    ...keys.map((k) => `      ${k}: \${{ secrets.${k} }}`),
    ...(sandbox
      ? [`      ${sandbox.envVar}: http://127.0.0.1:${SANDBOX_PORT}`]
      : []),
    "    steps:",
    "      - uses: actions/checkout@v4",
    ...(py
      ? [
          "      - uses: actions/setup-python@v5",
          '        with: { python-version: "3.12" }',
        ]
      : []),
    ...(ts || clis.some((c) => /^npm /.test(c))
      ? [
          "      - uses: actions/setup-node@v4",
          '        with: { node-version: "22" }',
        ]
      : []),
    ...uniq(list.map((r) => r.install)).map((l) => `      - run: ${l}`),
    ...clis.map((c) => `      - run: ${c}`),
    ...(sandbox ? [`      - run: ${SANDBOX_COMMAND} &`] : []),
    `      - run: bash ${shellName}`,
    "",
  ].join("\n");
  return {
    runs: list,
    shell,
    shellName,
    ci,
    ciName,
    secrets: ["FI_API_KEY", "FI_SECRET_KEY", ...keys],
  };
}
