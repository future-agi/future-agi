// R3 P3 (CR#1, CR#39): which runs wait on the user's code, and the live
// strip sentence for one of them. Pure.
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { fmtTimeRelative } from "src/sections/common/format/fmt";
import { runFlagsOf } from "src/sections/common/RunSources/snippets/lines";
import {
  CI_FILE,
  ciPathOfYaml,
} from "src/sections/common/RunSources/snippets/ci";
import { sandboxSummary } from "src/sections/common/RunSources/sourceModel";
import {
  WAIT_NOUNS,
  waitNounOf as ledgerWaitNounOf,
} from "src/sections/common/Runs/runLedger";
import { STAND_IN_EVENTS } from "src/sections/common/Connect/connectCopy";

// dataset integration FE-0 (owner Q12): the generated runner scripts and their CI
// workflow are not a real path, so no waiting surface prints a command, a
// script or a workflow. FE-1 adds the SDK's `futureagi run TARGET` line
// once its rows are green.

/** What a waiting run waits for, by its mode (S2 stand-ins). */
export const WAIT_FOR = Object.freeze({
  local: {
    noun: WAIT_NOUNS.local,
    as: "terminal",
    label: "Simulate your terminal",
    event: STAND_IN_EVENTS.terminal,
  },
  ci: {
    noun: WAIT_NOUNS.ci,
    as: "ci_job",
    label: "Simulate the CI job",
    event: STAND_IN_EVENTS.ci_job,
  },
  // L7c g5 (sdk:waiting-copy): one waiting noun, `results` (the run
  // status's own `Waiting for results`).
  upload: {
    noun: WAIT_NOUNS.upload,
    as: "upload",
    label: "Simulate the upload",
    event: STAND_IN_EVENTS.upload,
  },
});

/**
 * The waiting line as the run moves on: a sandbox run reads `Starting
 * sandbox · …` until its first row is in, `Running in the sandbox · …`
 * while rows arrive and `Ran in the sandbox · …` once every try is in (a
 * finished run never says it is starting). Other modes keep their title.
 * @param {{mode?: string, title?: string, where?: string}|null} wait
 * @param {{finished?: boolean}|null} arrival
 */
export function waitTitleOf(wait, arrival) {
  if (!wait) return null;
  if (wait.mode !== "sandbox" || !arrival || !wait.where) return wait.title;
  return `${arrival.finished ? "Ran in the sandbox" : "Running in the sandbox"} · ${wait.where}`;
}

/**
 * How a created run waits, by mode and kind (options-truth L1 G2):
 *   local   → `#4A waits for your terminal` (no command, dataset integration FE-0)
 *   ci      → `#4A waits for the CI job` (no workflow, dataset integration FE-0)
 *   sandbox → `Starting sandbox · <repo@branch or image>` (no command)
 *   remote  → null (nothing to wait for)
 *   upload  → `#4A waits for results` (no command)
 * @returns {{mode, title, as, simulate, command, lang}|null}
 */
// eslint-disable-next-line no-unused-vars
export function waitingOf(run = {}, draft = null, _opts = {}) {
  const cfg = draft?.config ?? run?.config ?? {};
  const kind =
    cfg.kind ??
    run?.kind ??
    (run?.target_type === "external" ? "upload" : "code");
  const mode =
    draft?.mode ??
    cfg.execution?.mode ??
    (kind === "upload" ? "upload" : "local");
  const label = run?.label ?? "This run";
  if (mode === "remote" || kind === "http") return null;
  if (mode === "sandbox") {
    const where = cfg.sandbox
      ? sandboxSummary(cfg.sandbox)
      : "Future AGI sandbox";
    return {
      mode,
      title: `Starting sandbox · ${where}`,
      where,
      as: null,
      simulate: null,
      command: null,
    };
  }
  if (kind === "upload" || mode === "upload")
    return {
      mode: "upload",
      title: `${label} waits for ${WAIT_NOUNS.upload}`,
      ...waitPart("upload"),
      command: null,
    };
  if (mode === "ci")
    return {
      mode,
      title: `${label} waits for the CI job`,
      ...waitPart("ci"),
      command: null,
      lang: "yaml",
    };
  return {
    mode: "local",
    title: `${label} waits for your terminal`,
    ...waitPart("local"),
    command: null,
    lang: "bash",
  };
}

/**
 * What a waiting run waits on, in words, by its mode (G2): `your terminal`
 * (local), `the CI job`, `results`, `the sandbox`. Pure.
 */
export function waitNounOf(run = {}) {
  // L7c g5: the one waiting noun lives in the run ledger.
  return ledgerWaitNounOf(run);
}

/**
 * The waiting drawer's line under its title, by the launched runs' modes
 * (`launched.r4`: one Level 3 draft per card; null for an upload card).
 */
export function launchedHowLine(drafts = []) {
  const modes = new Set(
    (drafts ?? []).map((d) =>
      !d ? "upload" : ["ci", "sandbox"].includes(d.mode) ? d.mode : "local",
    ),
  );
  const only = modes.size === 1 ? [...modes][0] : null;
  if (only === "ci")
    return "Commit the workflow below and let the CI job run it; each row lands here as the job uploads it and is scored.";
  if (only === "upload")
    return "Post your results as shown below; each row lands here as it is uploaded and scored.";
  if (only === "sandbox")
    return "The sandbox starts each run itself; each row lands here as it finishes and is scored.";
  if (only === "local")
    return "Run the command below on your machine; each row lands here as it finishes and is scored.";
  return "Start each run as shown below; each row lands here as it finishes and is scored.";
}

/** One noun for several runs: theirs when they share it, else `you`. */
export function waitNounOfAll(runs = []) {
  const nouns = [...new Set((runs ?? []).map((r) => waitNounOf(r)))];
  return nouns.length === 1 ? nouns[0] : "you";
}

function waitPart(mode) {
  const w = WAIT_FOR[mode];
  return { as: w.as, simulate: { label: w.label, event: w.event } };
}

/** A run whose results come from the user's code and are not all in. */
export function isExternalWaiting(run) {
  if (!run) return false;
  const st = getRunStatus(run);
  const fromCode =
    run.kind === "code" ||
    run.kind === "upload" ||
    run.target_type === "external" ||
    st.waiting;
  if (!fromCode) return false;
  if (st.waiting) return true;
  const p = run.progress ?? {};
  return st.active && (p.done ?? 0) < (p.total ?? 0);
}

const STALL_MS = 120000;

/** Live strip copy for one run (CR#1): pure, tested through the banner. */
export function waitingStripOf(run, session, now = Date.now()) {
  const total = session?.expected ?? run?.progress?.total ?? run?.examples ?? 0;
  const got = session?.received ?? run?.progress?.done ?? 0;
  const state = session?.state ?? (got > 0 ? "running" : "waiting");
  const beat = session?.last_heartbeat_at
    ? Date.parse(session.last_heartbeat_at)
    : null;
  if (state === "waiting" || (!got && state !== "connected")) {
    const noun = ledgerWaitNounOf(run);
    return {
      tone: "idle",
      text: `Waiting for ${noun} · 0 of ${total} received`,
    };
  }
  if (
    (state === "running" || state === "connected") &&
    beat &&
    now - beat > STALL_MS
  )
    return {
      tone: "stalled",
      text: `No upload for ${Math.round((now - beat) / 60000)} min.`,
    };
  if (state === "completed" || (total && got >= total))
    return { tone: "done", text: `All ${total} received · scoring` };
  const host = session?.host ? ` from ${session.host}` : "";
  const when = beat
    ? ` · last upload ${fmtTimeRelative(new Date(beat).toISOString(), { now })}`
    : "";
  return {
    tone: "live",
    text: `Connected${host} · ${got} of ${total} received${when}`,
  };
}

/**
 * L5 (I6, S3): every flag a run's own config set, for its printed command
 * (pure): tries (`--trials`), at a time, cost cap, time limit, the params
 * that differ from their declared default (`--param`) and the picked rows
 * (`--rows T2,T5`, labels when the run carries them).
 * @returns {{tries: number, limits: object, params: object,
 *   rows: string[]|null, flags: object}}
 */
export function commandArgsOf(run = {}) {
  const c = run?.config ?? {};
  const l = c.limits ?? {};
  const tries = Math.max(1, Number(l.tries ?? run?.trials) || 1);
  const rows =
    (run?.row_labels?.length ? run.row_labels : null) ??
    (c.row_labels?.length ? c.row_labels : null) ??
    (run?.row_ids?.length ? run.row_ids : null);
  const f = runFlagsOf({
    runId: run?.run_id ?? run?.id,
    limits: { ...l, tries },
    params: c.params?.values ?? {},
    schema: Array.isArray(c.params?.schema) ? c.params.schema : [],
    rowIds: rows ?? [],
  });
  return {
    tries,
    limits: {
      concurrency: l.concurrency ?? null,
      max_cost_usd: l.max_cost_usd ?? null,
      timeout_s: l.timeout_s ?? null,
    },
    params: f.params,
    rows,
    flags: f,
  };
}

/**
 * What a waiting terminal run prints (command, script, install). dataset integration
 * FE-0: always null (no generated runner is a real path; FE-1 prints the
 * SDK command). Kept as the one seam the banner and the tray read.
 * @returns {null}
 */
// eslint-disable-next-line no-unused-vars
export function terminalRunOf(_run, _opts) {
  return null;
}

/**
 * IT-R1 G1 P4 / L5 (I6): the command that starts a waiting run on the
 * user's machine (the same composer as Review's `Start it`, with every
 * flag the run set); null when the run is not started from a terminal.
 */
export const terminalCommandOf = (run, opts) =>
  terminalRunOf(run, opts)?.command ?? null;

/**
 * L3 G5 (S2): the stand-in a waiting run offers, by its execution mode:
 * `Simulate your terminal` (local), `Simulate the CI job` (CI), `Simulate
 * the upload` (uploads); null for a run that starts itself. Pure.
 */
export function standInOf(run = {}) {
  const w = waitingOf(run);
  return w?.simulate ?? null;
}

/** Where the CI workflow lives (the same path the YAML's header names). */
export const CI_WORKFLOW_PATH = CI_FILE;
/** L3 G6 (S3): the path a run's workflow names in its header (fi-experiment-4a.yml). */
export const ciWorkflowPathOf = (yaml) => ciPathOfYaml(yaml, CI_WORKFLOW_PATH);

const namesIn = (text, scope) => [
  ...new Set(
    [
      ...String(text ?? "").matchAll(
        new RegExp(`${scope}\\.([A-Z0-9_]+)`, "g"),
      ),
    ].map((m) => m[1]),
  ),
];

/**
 * L2 G3 (L4): the CI job's own setup, read off the workflow text: save the
 * file, add the secrets it reads, set the variables it reads, run it. Pure.
 * @param {string} yaml
 * @returns {string[]}
 */
export function ciStepsOf(yaml) {
  const secrets = namesIn(yaml, "secrets");
  const vars = namesIn(yaml, "vars");
  const triggers = ["pull_request", "push", "workflow_dispatch"].filter((t) =>
    new RegExp(`^\\s+${t}:`, "m").test(String(yaml ?? "")),
  );
  const ways = [
    triggers.includes("workflow_dispatch")
      ? "run the workflow from the Actions tab"
      : null,
    triggers.includes("pull_request") ? "open a pull request" : null,
    triggers.includes("push") ? "push a commit" : null,
  ].filter(Boolean);
  const how = ways.length
    ? `${ways.join(" or ").replace(/^./, (c) => c.toUpperCase())}`
    : "Run the workflow";
  return [
    `Save the workflow as ${ciWorkflowPathOf(yaml)}`,
    secrets.length ? `Add the repository secrets ${secrets.join(", ")}` : null,
    vars.length ? `Set the repository variables ${vars.join(", ")}` : null,
    `${how}; each row lands here as the job uploads it and is scored.`,
  ].filter(Boolean);
}

/**
 * The body a stand-in (`Simulate your terminal`, `Simulate the CI job`)
 * posts for a created run: the same as the drawer's Simulate (C15), so
 * the run fills with its own config, tries and scope. Pure.
 */
export function standInBodyOf(run = {}, { datasetId, config } = {}) {
  const cfg = run?.config ?? config ?? {};
  const wait = waitingOf({ ...run, config: cfg });
  return {
    rows: "all",
    as: wait?.as ?? "terminal",
    dataset_id: datasetId ?? run?.dataset_id ?? null,
    entry_id: cfg.framework?.id ?? cfg.harness?.id ?? null,
    tries: cfg.limits?.tries ?? run?.trials ?? 1,
    ...(run?.config || config ? { config: cfg } : {}),
  };
}

/**
 * L2 G3 (L4): what the tray offers next to `Open`, by how the run runs:
 * your terminal → `How to run`, the CI job → `Open the CI job`, uploads →
 * `How to upload`; a sandbox, an endpoint or a connected machine runs it
 * itself → nothing but `Open`. Pure.
 * @returns {{kind: "run"|"ci"|"upload", label: string}|null}
 */
export function trayActionOf(run = {}) {
  const cfg = run?.config ?? {};
  const exec = cfg.execution ?? {};
  const kind =
    cfg.kind ??
    run?.kind ??
    (run?.target_type === "external" ? "upload" : null);
  if (exec.machine_id) return null;
  if (["sandbox", "remote", "platform", "connect"].includes(exec.mode))
    return null;
  if (exec.mode === "ci") return { kind: "ci", label: "Open the CI job" };
  if (kind === "upload" || exec.mode === "upload" || !exec.mode)
    return { kind: "upload", label: "How to upload" };
  if (exec.mode === "local") return { kind: "run", label: "How to run" };
  return null;
}
