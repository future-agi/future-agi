import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { parse } from "yaml";
import { catalogEntry } from "../catalog";
import { ciExperimentWorkflow, ciWorkflow } from "../snippets/ci";
import { ciCommentStep } from "../snippets/ciComment";

const experimentId = "10000000-0000-4000-8000-000000000001";
const baselineRunId = "30000000-0000-4000-8000-000000000003";
const candidates = [
  "20000000-0000-4000-8000-000000000002",
  "40000000-0000-4000-8000-000000000004",
];
const directories = [];
const output = () => {
  const directory = mkdtempSync(join(tmpdir(), "ci-comment-"));
  directories.push(directory);
  const file = join(directory, "output");
  writeFileSync(file, "");
  return file;
};

afterEach(() => {
  directories.splice(0).forEach((dir) => rmSync(dir, { recursive: true }));
});

// Execute the generated shell, not the runner or network clients. Each fake
// records its actual arguments; no fixture reads the user's keys or services.
const uploadStub = `
python() {
  while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "--run" ]]; then printf 'uploaded:%s\\n' "$2"; fi
    shift
  done
  return "\${UPLOAD_EXIT:-0}"
}
npx() { python "$@"; }
`;
const commentStubs = `
curl() {
  local candidate='' baseline='' response=''
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --data-urlencode)
        case "$2" in
          run_id=*) candidate="\${2#run_id=}" ;;
          baseline_run_id=*) baseline="\${2#baseline_run_id=}" ;;
        esac
        shift ;;
      --output) response="$2"; shift ;;
    esac
    shift
  done
  printf 'request:%s:%s\\n' "$candidate" "$baseline"
  printf 'Run: %s; baseline: %s\\n' "$candidate" "$baseline" > "$response"
}
jq() {
  if [[ "$1" == "-er" ]]; then
    cat "\${@: -1}"
  else
    return "\${VERDICT_EXIT:-0}"
  fi
}
gh() {
  [[ "$1 $2 $3 $4" == "pr comment 17 --body-file" ]] || return 2
  printf 'comment:'
  cat "$5"
}
`;

const shell = (text, env = {}) =>
  spawnSync("/bin/bash", ["-c", text], {
    encoding: "utf8",
    timeout: 5000,
    // Deliberately exclude process.env: no inherited credentials or services.
    env: { PATH: "/usr/bin:/bin", ...env },
  });

const comment = parse(ciCommentStep({ experimentId, baselineRunId }))[0];
function post(runId, outcome = "success", verdict = "0") {
  return shell(`${commentStubs}\n${comment.run}`, {
    FI_RUN_ID: runId,
    FI_EVALUATION_OUTCOME: outcome,
    FI_API_URL: "https://api.example.invalid",
    FI_APP_URL: "https://app.example.invalid",
    FI_API_KEY: "fixture-api",
    FI_SECRET_KEY: "fixture-secret",
    GH_TOKEN: "fixture-token",
    GH_REPO: "example/repository",
    PR_NUMBER: "17",
    VERDICT_EXIT: verdict,
  });
}

function uploadStep(entry) {
  return parse(ciWorkflow({ entry })).jobs.experiment.steps.find(
    (step) => step.id === "fi_evaluation",
  );
}

function upload(step, runId, exit = "0") {
  const file = output();
  const expression = "${{ vars.FI_RUN_ID }}";
  const result = shell(
    `${uploadStub}\n${step.run.replaceAll(expression, runId)}`,
    {
      FI_EVALUATION_RUN_ID: step.env.FI_EVALUATION_RUN_ID.replaceAll(
        expression,
        runId,
      ),
      GITHUB_OUTPUT: file,
      UPLOAD_EXIT: exit,
    },
  );
  return { result, published: readFileSync(file, "utf8") };
}

describe("evaluation upload to PR comment", () => {
  it.each(["langgraph", "vercel_ai", "claude_code"])(
    "comments on each execution's uploaded run for %s with a fixed baseline",
    (entryId) => {
      const step = uploadStep(catalogEntry(entryId));
      expect(step.shell).toBe("bash");
      expect(step.env).toEqual({
        FI_EVALUATION_RUN_ID: "${{ vars.FI_RUN_ID }}",
      });
      expect(comment.env.FI_RUN_ID).toBe(
        "${{ steps.fi_evaluation.outputs.run_id }}",
      );
      expect(comment.env.FI_EVALUATION_OUTCOME).toBe(
        "${{ steps.fi_evaluation.outcome }}",
      );
      for (const runId of candidates) {
        const { result, published } = upload(step, runId);
        expect(result.status, result.stderr).toBe(0);
        expect(result.stdout).toBe(`uploaded:${runId}\n`);
        expect(published).toBe(`run_id=${runId}\n`);
        const posted = post(published.trim().slice("run_id=".length));
        expect(posted.status, posted.stderr).toBe(0);
        expect(posted.stdout).toBe(
          `request:${runId}:${baselineRunId}\n` +
            `comment:Run: ${runId}; baseline: ${baselineRunId}\n`,
        );
      }
    },
  );

  it("publishes the exact run passed by a whole-experiment job", () => {
    const workflow = parse(
      ciExperimentWorkflow({
        runs: [
          {
            label: "#1A",
            entry: catalogEntry("langgraph"),
            runId: candidates[1],
          },
        ],
      }),
    );
    const step = workflow.jobs["run-1a"].steps.find(
      (item) => item.id === "fi_evaluation",
    );
    expect(step.env).toEqual({ FI_EVALUATION_RUN_ID: candidates[1] });
    const { result, published } = upload(step, candidates[0]);
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toBe(`uploaded:${candidates[1]}\n`);
    expect(published).toBe(`run_id=${candidates[1]}\n`);
  });

  it("does not publish a run output after the evaluation or upload fails", () => {
    const { result, published } = upload(
      uploadStep(catalogEntry("langgraph")),
      candidates[0],
      "9",
    );
    expect(result.status).toBe(9);
    expect(published).toBe("");
  });

  it.each(["failure", "cancelled", "skipped", ""])(
    "refuses an evaluation outcome of %s even with an existing output",
    (outcome) => {
      const posted = post(candidates[0], outcome);
      expect(posted.status).toBe(1);
      expect(posted.stdout).toBe("");
      expect(posted.stderr).toBe(
        "Evaluation/upload did not succeed; no comment posted.\n",
      );
    },
  );

  it.each(["", "not-a-run", baselineRunId])(
    "refuses an absent or invalid candidate before any request: %s",
    (runId) => {
      const posted = post(runId);
      expect(posted.status).toBe(1);
      expect(posted.stdout).toBe("");
    },
  );

  it("skips earlier job failures and forks rather than using always", () => {
    expect(comment.if).toBe(
      "${{ success() && github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository }}",
    );
  });

  it("posts the current run before failing a regression verdict", () => {
    const posted = post(candidates[1], "success", "1");
    expect(posted.status).toBe(1);
    expect(posted.stdout).toBe(
      `request:${candidates[1]}:${baselineRunId}\n` +
        `comment:Run: ${candidates[1]}; baseline: ${baselineRunId}\n`,
    );
  });
});
