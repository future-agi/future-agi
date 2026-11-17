// L7c g5 (run lifecycle): the one ledger's waiting noun, running bounded by
// At once, errored tries, the bar's scored count, provenance (line, JSON,
// parameters), the steps note, the backoff line and the where-it-ran mode.
import { describe, expect, it } from "vitest";
import {
  WAIT_NOUNS,
  erroredRowsOf,
  ledgerLine,
  ledgerOf,
  runLedgerLine,
  waitNounOf,
  waitingPhraseOf,
} from "../runLedger";
import { runProgressOf } from "../runStatus";
import { provenanceLine, provenanceOf } from "../provenanceModel";
import {
  provenanceJsonOf,
  runSettingsRows,
  settingsRows,
} from "../provenanceJson";
import { rescoreConfirmBody } from "../rescoreCopyModel";
import { STEPS_NOTE } from "../runHowItRan";
import { backoffLine } from "../errorCause";
import { whereRows } from "../provenanceExtras";

const rec = (row, visibility, extra = {}) => ({
  example_id: `r${row}`,
  visibility,
  status: extra.status ?? (visibility === "error" ? "error" : "ok"),
  scores:
    visibility === "completed"
      ? { "eval:correctness": { value: 1, passed: true } }
      : {},
  ...extra,
});

describe("one waiting noun (sdk:waiting-copy)", () => {
  it("an upload run waits for results, never your code", () => {
    const upload = { target_type: "external", status: "WaitingForResults" };
    expect(waitNounOf(upload)).toBe("results");
    expect(waitingPhraseOf(upload)).toBe("waiting for results");
    expect(waitNounOf({ config: { kind: "upload" } })).toBe(WAIT_NOUNS.upload);
    expect(waitNounOf({ config: { execution: { mode: "ci" } } })).toBe(
      "the CI job",
    );
    expect(Object.values(WAIT_NOUNS).join(" ")).not.toMatch(/your code/);
  });

  it("a waiting run's ledger says its rows wait for results, not queued", () => {
    const ledger = ledgerOf({
      records: [1, 2, 3].map((n) => rec(n, "queued")),
      primaryKey: "eval:correctness",
    });
    const run = {
      target_type: "external",
      status: "WaitingForResults",
      ledger,
    };
    expect(runLedgerLine(run)).toBe("3 rows waiting for results");
    expect(runLedgerLine({ ...run, status: "Running" })).toBe("3 rows queued");
  });
});

describe("running is bounded by At once (ledger:leaderboard-progress)", () => {
  const records = [
    ...[1, 2].map((n) => rec(n, "completed")),
    ...[3, 4, 5, 6].map((n) => rec(n, "running")), // in, not scored yet
    ...[7, 8, 9, 10, 11, 12].map((n) => rec(n, "queued")),
  ];
  it("At once 1: one row running, the unscored rows are scoring", () => {
    const l = ledgerOf({
      records,
      primaryKey: "eval:correctness",
      atOnce: 1,
      active: true,
    });
    expect(l.rows.running).toBe(1);
    expect(l.rows.scoring).toBe(4);
    expect(l.rows.queued).toBe(5);
    expect(l.at_once).toBe(1);
    expect(ledgerLine(l)).toBe(
      "2 rows scored · 1 running · 4 scoring · 5 queued",
    );
  });
  it("At once 10: never more running than rows left; none once inactive", () => {
    const l = ledgerOf({
      records,
      primaryKey: "eval:correctness",
      atOnce: 10,
      active: true,
    });
    expect(l.rows.running).toBe(6);
    expect(l.rows.queued).toBe(0);
    const idle = ledgerOf({
      records,
      primaryKey: "eval:correctness",
      atOnce: 10,
      active: false,
    });
    expect(idle.rows.running).toBe(0);
    expect(idle.rows.queued).toBe(6);
  });
  it("without At once the ledger keeps its old shape", () => {
    const l = ledgerOf({ records, primaryKey: "eval:correctness" });
    expect(l.rows.running).toBe(4);
    expect("at_once" in l).toBe(false);
  });
  it("the bar counts rows scored, so it is not full while rows score", () => {
    const p = runProgressOf({ progress: { done: 20, total: 20, scored: 14 } });
    expect(p.finished).toBe(14);
    expect(p.pct).toBe(70);
    expect(runProgressOf({ progress: { done: 5, total: 10 } }).pct).toBe(50);
  });
});

describe("errored tries inside completed rows (ledger:r1)", () => {
  it("counts them at try level with their cause", () => {
    const err = (row, trial) =>
      rec(row, "error", {
        trial,
        status: "error",
        error: "lookup_invoice timeout after 30s",
      });
    const l = ledgerOf({
      records: [
        { ...rec(1, "completed"), trial: 1 },
        err(1, 2),
        { ...rec(2, "completed"), trial: 1 },
        err(2, 2),
        err(2, 3),
      ],
      primaryKey: "eval:correctness",
    });
    expect(l.rows.scored).toBe(2);
    const line = ledgerLine(l);
    expect(line).toMatch(/^2 rows scored · 3 tries errored \(/);
    expect(erroredRowsOf({ ledger: l })).toBe(0);
    expect(
      erroredRowsOf({ ledger: { rows: { errored: { a: 2, b: 1 } } } }),
    ).toBe(3);
    expect(erroredRowsOf({}, 7)).toBe(7);
  });
});

describe("provenance (identity:provenance-line, identity:provenance-card)", () => {
  const http = {
    run_id: "r",
    label: "#9A",
    target_type: "http",
    config: {
      kind: "http",
      endpoint: { url: "https://helpdesk.globex.test/api/answer" },
      limits: { tries: 3, concurrency: 5, max_cost_usd: 2 },
    },
  };
  it("an endpoint line shows its tries and cost cap", () => {
    const line = provenanceLine(provenanceOf(http));
    expect(line).toMatch(/^Endpoint · HTTP · helpdesk\.globex\.test/);
    expect(line).toMatch(/3 tries/);
    expect(line).toMatch(/cap \$2/);
  });
  it("the card JSON states the data scope, eval versions, reducer and metadata", () => {
    const j = provenanceJsonOf({
      ...http,
      dataset_version: { number: 6 },
      splits: [{ name: "smoke" }],
      examples: 20,
      sample: { n: 10, seed: 3, of: 20 },
      trials: 3,
      concurrency: 5,
      eval_versions: { "eval:correctness": 2 },
      trial_reducer: null,
      metadata: { source: "ui" },
    });
    expect(j.data).toEqual({
      dataset_version: 6,
      splits: ["smoke"],
      rows: 20,
      sample: { n: 10, seed: 3, of: 20 },
    });
    expect(j.eval_versions).toEqual({ "eval:correctness": 2 });
    expect(j.trial_reducer).toBe("mean");
    expect(j.metadata).toEqual({ source: "ui" });
    expect(j.config.limits.tries).toBe(3);
  });
  it("Parameters lists model params and settings a run ran with", () => {
    const rows = settingsRows({
      model: { params: { temperature: 0.7, max_tokens: 1024 } },
      limits: { tries: 3, concurrency: 5, max_cost_usd: 2 },
    });
    expect(rows.map((r) => `${r.name}=${r.value}`)).toEqual([
      "temperature=0.7",
      "max_tokens=1024",
      "tries=3",
      "at_a_time=5",
      "cost_cap_usd=2",
    ]);
  });
  it("Run settings fall back to the run's own tries and at a time (#4A)", () => {
    const rows = runSettingsRows({
      config: { kind: "agent" },
      trials: 3,
      concurrency: 2,
      dataset_version: { number: 6 },
      splits: [],
      examples: 248,
      eval_versions: { "eval:correctness": 2, "eval:task_completion": 1 },
      trial_reducer: null,
    });
    expect(rows.map((r) => `${r.label}=${r.value}`)).toEqual([
      "Tries=3",
      "At a time=2",
      "Data=v6 · 248 rows",
      "Evals=correctness v2, task_completion v1",
      "Each row=mean of 3 tries",
    ]);
    const capped = runSettingsRows({
      config: { limits: { tries: 1, concurrency: 10, max_cost_usd: 0.3 } },
      trials: 1,
      sample: { n: 25, seed: 7, of: 248 },
    });
    expect(capped.map((r) => r.value)).toEqual([
      "1",
      "10",
      "$0.30",
      "25 random rows (seed 7)",
    ]);
  });
  it("Re-score names the run's own evals it does not judge again", () => {
    const body = rescoreConfirmBody({
      runs: [{ label: "#4A", attempt: 1, attempts_count: 1 }],
      evalNames: ["Correctness"],
      runOnlyEvals: ["Task completion", "Tool-call accuracy"],
    });
    expect(body).toMatch(/Correctness scores the outputs of #4A again/);
    expect(body).toMatch(
      /Task completion and Tool-call accuracy are scored by the run itself and keep their scores\.$/,
    );
    expect(
      rescoreConfirmBody({
        runs: [{ label: "#4A" }],
        evalNames: ["Correctness"],
      }),
    ).not.toMatch(/scored by the run itself/);
  });
  it("the how-it-ran note names no harness", () => {
    expect(STEPS_NOTE).not.toMatch(/harness/i);
  });
  it("a machine run's mode names whose machine, never This machine", () => {
    const rows = whereRows({
      where: { mode: "local", label: "voice-worker@acme-gpu-01" },
      owner: { name: "Ravi Menon" },
    });
    expect(rows[0]).toEqual({ label: "Mode", value: "Ravi Menon's machine" });
    expect(JSON.stringify(rows)).not.toMatch(/This machine/);
  });
});

describe("backoff line (info:rerun-failed:backoff-line)", () => {
  it("agrees with the count and says what the engine does", () => {
    expect(
      backoffLine([
        {
          errors_by_cause: [
            { cause: "RateLimitError", rows: 1, retry_after_s: 2.1 },
          ],
        },
      ]),
    ).toBe(
      "1 rate-limited row runs again after the other rows, once the 2.1 s wait its error names is over.",
    );
    expect(
      backoffLine([
        {
          ledger: { primary_key: "eval:task_success" },
          errors_by_cause: [{ cause: "RateLimitError", rows: 2 }],
        },
      ]),
    ).toBe("");
  });
});
