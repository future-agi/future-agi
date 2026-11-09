// CI snippets (S3, options-truth L1 G2, L3 G6). One workflow file per run,
// `.github/workflows/fi-experiment-<run label>.yml` (Start it's whole-
// experiment file is fi-experiment.yml, uploads fi-upload.yml), one job:
// checkout, set up the language, install the exact packages, then run the
// drawer's runner with the run's id and limits, gated by `--fail-under`.
// The same file shows on the L3 CI panel and its download; the panel's
// "Commit these" step offers the files the job runs.
//   steps({entry, ids, ci, limits, models, depth}) →
//     [{n, id, title, lang, code, fileName?}]
// Pure.
import { catalogEntry } from "../catalog";
import { vendorKeysOf } from "../declaredUses";
import { CI_FILE as CI_NAME, CI_PATH, runnerCommand } from "./lines";
import { pipPackages, traceUploadSteps } from "./python";
import { traceUploadStepsTs } from "./typescript";
import {
  HARNESS_FILE,
  HARNESS_PACKAGES,
  harnessScript,
} from "./prototypeHarness";
import { defaultRecipe, installLine, keyEnvOf } from "../recipe/recipeModel";

/** Start it's whole-experiment workflow (the file name alone is CI_NAME). */
export const CI_FILE = CI_PATH;
export { CI_NAME };

/** `#4A` → `4a`: the run label as a file-name part. */
export const ciLabelSlug = (label) =>
  String(label ?? "")
    .replace(/^#/, "")
    .replace(/[^A-Za-z0-9]+/g, "")
    .toLowerCase();

/**
 * One run's CI workflow (S3): `.github/workflows/fi-experiment-4a.yml`, so
 * two runs, Start it (fi-experiment.yml) and uploads (fi-upload.yml) never
 * share a path.
 */
export function ciWorkflowPath(runLabel) {
  const slug = ciLabelSlug(runLabel);
  return `.github/workflows/fi-experiment${slug ? `-${slug}` : "-run"}.yml`;
}

/**
 * The path a workflow's own header names (`# .github/workflows/<file>.yml ·
 * …`, the first line of every YAML this module prints), else `fallback`.
 * Screens that show a YAML name the file from the YAML itself, so the
 * waiting panel, the tray and the panel never disagree with it. Pure.
 */
export function ciPathOfYaml(yaml, fallback = CI_PATH) {
  const m = /^#\s*(\.github\/workflows\/[\w.-]+\.ya?ml)\b/.exec(
    String(yaml ?? ""),
  );
  return m ? m[1] : fallback;
}
/** The pass rate a pull request needs, by default. */
export const CI_THRESHOLD = 0.85;
export const CI_TRIGGERS = Object.freeze([
  { id: "pull_request", label: "Every pull request" },
  { id: "push", label: "Every push to main" },
  { id: "workflow_dispatch", label: "When I start it" },
  { id: "schedule", label: "Every night" },
]);

const isTs = (e) =>
  (e?.languages ?? []).includes("typescript") &&
  !(e?.languages ?? []).includes("python");

/** The packages the harness runner imports (it imports fi_instrumentation). */
export const CI_HARNESS_PACKAGES = HARNESS_PACKAGES;

/**
 * options-truth L2 G4 (C3): whether a run kind can run from a CI job, and
 * why not.
 * @returns {{ok: boolean, reason: string|null}}
 */
// eslint-disable-next-line no-unused-vars
export function ciOffer(entry) {
  return { ok: true, reason: null };
}

/** The recipe a harness CI job installs and keys (the default when none). */
const recipeOf = (entry, recipe) =>
  recipe ?? (entry?.kind === "harness" ? defaultRecipe(entry) : null);

/**
 * The models whose keys a CI job needs (pure): the run's models; for a
 * harness without any, its recipe's model. One list for the YAML and the
 * key row beside it, so the two never disagree.
 */
export function ciKeyModels({ entry, models = [], recipe, params } = {}) {
  // a changed `model` param is the model the job calls (S5): its key too
  const chosen = params?.model ? [String(params.model)] : [];
  const list = [...new Set([...chosen, ...(models ?? []).filter(Boolean)])];
  if (list.length) return list;
  const r = recipeOf(entry, recipe);
  return r?.model ? [r.model] : [];
}

/**
 * The repository secrets a run needs: FI keys plus one key per vendor of
 * its models (none when it declares no model, as the key row says).
 */
export function ciSecrets({ entry, models = [], recipe, params } = {}) {
  const list = ciKeyModels({ entry, models, recipe, params });
  const keys =
    entry?.kind === "harness"
      ? [...new Set(list.map((m) => keyEnvOf(m, entry.id)).filter(Boolean))]
      : vendorKeysOf(list);
  return ["FI_API_KEY", "FI_SECRET_KEY", ...keys];
}

/** The `on:` block of a trigger. */
function onBlock(trigger) {
  if (trigger === "push")
    return ["on:", "  push:", "    branches: [main]", "  workflow_dispatch:"];
  if (trigger === "schedule")
    return [
      "on:",
      "  schedule:",
      '    - cron: "17 3 * * *"',
      "  workflow_dispatch:",
    ];
  if (trigger === "workflow_dispatch") return ["on:", "  workflow_dispatch:"];
  return ["on:", "  pull_request:", "  workflow_dispatch:"];
}

/**
 * The run step's command (code / TypeScript / harness). It carries
 * every limit the screen set (S3): tries, at a time, cost cap, time limit,
 * and the form's params and rows (`flags`, runFlagsOf).
 */
export function ciRunCommand({
  entry,
  ci = {},
  limits = {},
  flags = {},
  runId,
} = {}) {
  const threshold = ci.threshold ?? CI_THRESHOLD;
  const l = limits ?? {};
  const f = flags ?? {};
  const common = {
    // one run's own file reads the repository variable; Start it's file
    // for several runs names each run's id (one variable cannot hold two)
    runId: runId || "${{ vars.FI_RUN_ID }}",
    trials: Math.max(1, Number(l.tries ?? f.trials) || 1),
    limits: {
      concurrency: Math.max(1, Number(l.concurrency) || 1),
      maxCostUsd: l.maxCostUsd ?? l.max_cost_usd ?? f.limits?.maxCostUsd,
      timeoutS: l.timeoutS ?? l.timeout_s ?? f.limits?.timeoutS,
    },
    rows: f.rows,
  };
  const withCounts = (cmd) => {
    // --trials and --concurrency always show in CI (a reviewer reads them)
    let out = cmd;
    if (!/--trials /.test(out)) out += ` --trials ${common.trials}`;
    if (!/--concurrency /.test(out))
      out += ` --concurrency ${common.limits.concurrency}`;
    return out;
  };
  if (entry?.kind === "harness")
    return `${withCounts(runnerCommand({ lang: "harness", ...common }))} --fail-under ${threshold}`;
  const spec = entry?.agent?.spec ?? "agent.py:target";
  const lang = isTs(entry) ? "typescript" : "python";
  return `${withCounts(runnerCommand({ lang, entry: spec, params: f.params, ...common }))} --fail-under ${threshold}`;
}

/** The install line of the job (the exact packages). */
export function ciInstall(entry, npmPackages) {
  if (entry?.kind === "harness") return `pip install ${CI_HARNESS_PACKAGES}`;
  if (isTs(entry))
    return `npm i ${npmPackages ?? "@traceai/fi-core @opentelemetry/api tsx"}`;
  return `pip install ${pipPackages(entry)}`;
}

/**
 * A harness job's setup lines (pure): Python for the runner, Node when the
 * recipe installs the CLI with npm, then the recipe's own install line.
 */
function harnessSetup(entry, recipe) {
  const r = recipeOf(entry, recipe);
  const cli = r ? installLine(r) : null;
  return [
    "      - uses: actions/setup-python@v5",
    "        with:",
    '          python-version: "3.12"',
    ...(cli && /^npm /.test(cli)
      ? [
          "      - uses: actions/setup-node@v4",
          "        with:",
          '          node-version: "22"',
        ]
      : []),
    "      - name: Install the runner",
    `        run: ${ciInstall(entry)}`,
    ...(cli
      ? [
          `      - name: Install ${entry?.name ?? "the CLI"}`,
          `        run: ${cli}`,
        ]
      : []),
  ];
}

/**
 * One run's job (pure): `  <jobId>:` and its env, checkout, setup, install
 * and the run step. `workdir` runs the steps in the run's folder (Start
 * it's file for several runs); `runId` names the run instead of FI_RUN_ID.
 */
export function ciJobLines({
  entry,
  ids = {},
  ci = {},
  limits = {},
  models = [],
  depth,
  npm,
  recipe,
  flags,
  jobId = "experiment",
  workdir = null,
  runId = null,
} = {}) {
  const e = entry ?? catalogEntry("langgraph");
  const ts = isTs(e);
  const threshold = ci?.threshold ?? CI_THRESHOLD;
  const secrets = ciSecrets({
    entry: e,
    models,
    recipe,
    params: (flags ?? ids.run)?.params,
  });
  const setup = ts
    ? [
        "      - uses: actions/setup-node@v4",
        "        with:",
        '          node-version: "22"',
      ]
    : [
        "      - uses: actions/setup-python@v5",
        "        with:",
        '          python-version: "3.12"',
      ];
  const install =
    e?.kind === "harness"
      ? harnessSetup(e, recipe)
      : [
          ...setup,
          "      - name: Install",
          `        run: ${ciInstall(e, npm)}`,
        ];
  return [
    `  ${jobId}:`,
    "    runs-on: ubuntu-latest",
    ...(workdir
      ? ["    defaults:", "      run:", `        working-directory: ${workdir}`]
      : []),
    "    env:",
    ...secrets.map((k) => `      ${k}: \${{ secrets.${k} }}`),
    "    steps:",
    "      - uses: actions/checkout@v4",
    ...install,
    "      - name: Run every row and upload the results",
    `        run: ${ciRunCommand({ entry: e, ci: { threshold }, limits, depth, flags: flags ?? ids.run, runId })}`,
  ];
}

/**
 * The workflow file.
 * @param {{entry?: object, ids?: object, ci?: {provider, trigger, threshold},
 *   limits?: {tries, concurrency}, models?: string[], depth?: string,
 *   npm?: string}} p
 * @returns {string}
 */
export function ciWorkflow({
  entry,
  ids = {},
  ci = {},
  limits = {},
  models = [],
  depth,
  npm,
  recipe,
  flags,
} = {}) {
  const e = entry ?? catalogEntry("langgraph");
  const trigger = ci.trigger ?? "pull_request";
  const threshold = ci.threshold ?? CI_THRESHOLD;
  const offer = ciOffer(e);
  const path = ciWorkflowPath(ids.runLabel);
  if (!offer.ok) return `# ${path}\n# ${offer.reason}\n`;
  const label = ids.runLabel ? `run "${ids.runLabel}"` : "this run";
  return [
    `# ${path} · ${e?.name ?? "your agent"} on "${ids.datasetName || "this dataset"}"`,
    `# Runs ${label} (FI_RUN_ID, a repository variable) and uploads every row;`,
    `# the check fails below a pass rate of ${threshold}.`,
    `name: ${path
      .split("/")
      .pop()
      .replace(/\.yml$/, "")}`,
    ...onBlock(trigger),
    "jobs:",
    ...ciJobLines({
      entry: e,
      ids,
      ci: { ...ci, threshold },
      limits,
      models,
      depth,
      npm,
      recipe,
      flags,
    }),
    "",
  ].join("\n");
}

/**
 * Start it's whole-experiment file (`.github/workflows/fi-experiment.yml`,
 * S3) for several runs set to CI (pure): one job per run, each in its
 * run's folder (`run-4A/`) with its own install, keys, limits and run id.
 * @param {{ordinal?: number|null, datasetName?: string, runs: {label,
 *   name?, entry, runId, folder, ci?, limits?, models?, recipe?, flags?,
 *   depth?}[]}} p
 */
export function ciExperimentWorkflow({ ordinal, datasetName, runs = [] } = {}) {
  const triggers = [
    ...new Set(runs.map((r) => r.ci?.trigger ?? "pull_request")),
  ];
  const n = ordinal ?? "next";
  return [
    `# ${CI_FILE} · starts the ${runs.length} CI runs of Experiment #${n} on "${datasetName || "this dataset"}"`,
    "# One job per run, each in its run's folder; each check fails below its run's pass rate.",
    "name: fi-experiment",
    ...onBlock(triggers.length === 1 ? triggers[0] : "workflow_dispatch"),
    "jobs:",
    ...runs.flatMap((r) =>
      ciJobLines({
        entry: r.entry,
        ids: { runLabel: r.label },
        ci: r.ci ?? {},
        limits: r.limits ?? {},
        models: r.models ?? [],
        depth: r.depth,
        recipe: r.recipe,
        flags: r.flags,
        jobId: ciLabelSlug(r.label) ? `run-${ciLabelSlug(r.label)}` : "run",
        workdir: r.folder,
        runId: r.runId,
      }),
    ),
    "",
  ].join("\n");
}

/**
 * The files the CI job runs, to commit next to the workflow (pure): the
 * runner and the agent file of a code run, the harness runner of a harness
 * run. `[{fileName, text}]` (the agent file is the example to adapt).
 */
export function ciCommitFiles({ entry, ids = {}, recipe } = {}) {
  const e = entry ?? catalogEntry("langgraph");
  if (e?.kind === "harness")
    return [
      {
        fileName: HARNESS_FILE,
        text: harnessScript({ entry: e, ids, recipe: recipeOf(e, recipe) }),
      },
    ];
  const s = isTs(e)
    ? traceUploadStepsTs({ entry: e, ids })
    : traceUploadSteps({ entry: e, ids });
  const agent = (s.steps ?? []).find((x) => x.id === "agent");
  return [
    { fileName: s.fileName, text: s.file },
    ...(agent?.fileName
      ? [{ fileName: agent.fileName, text: agent.code }]
      : []),
  ];
}

/** `gh secret set …` for each secret, then the run id variable (its value). */
export function ciSecretCommands({ entry, models, recipe, params, runId }) {
  return [
    ...ciSecrets({ entry, models, recipe, params }).map(
      (k) => `gh secret set ${k}`,
    ),
    `gh variable set FI_RUN_ID --body ${runId || "<run id>"}`,
  ].join("\n");
}

export function steps({
  entry,
  ids = {},
  ci,
  limits,
  models,
  depth,
  recipe,
} = {}) {
  const path = ciWorkflowPath(ids.runLabel);
  return [
    {
      n: 1,
      id: "file",
      title: path,
      lang: "yaml",
      code: ciWorkflow({ entry, ids, ci, limits, models, depth, recipe }),
      fileName: path,
    },
    {
      n: 2,
      id: "secrets",
      title: "Repository secrets and the run id",
      lang: "bash",
      code: ciSecretCommands({
        entry,
        models,
        recipe,
        runId: ids.runId,
      }),
    },
  ];
}
