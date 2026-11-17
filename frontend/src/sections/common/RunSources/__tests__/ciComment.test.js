import { describe, expect, it } from "vitest";
import { ciCommentStep } from "../snippets/ciComment";

const ids = {
  experimentId: "10000000-0000-4000-8000-000000000001",
  baselineRunId: "30000000-0000-4000-8000-000000000003",
};
const expected = [
  "# Add after evaluation/upload in a pull_request job on ubuntu-latest.",
  "# The Your code step id is fi_evaluation; it outputs run_id after success.",
  "# Job permissions: contents: read, pull-requests: write.",
  "# FI_API_URL is the API base URL; FI_APP_URL is the app origin.",
  "- name: Comment on evaluation results against the baseline",
  "  if: ${{ success() && github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository }}",
  "  env:",
  "    FI_RUN_ID: ${{ steps.fi_evaluation.outputs.run_id }}",
  "    FI_EVALUATION_OUTCOME: ${{ steps.fi_evaluation.outcome }}",
  "    FI_API_URL: ${{ vars.FI_API_URL }}",
  "    FI_APP_URL: ${{ vars.FI_APP_URL }}",
  "    FI_API_KEY: ${{ secrets.FI_API_KEY }}",
  "    FI_SECRET_KEY: ${{ secrets.FI_SECRET_KEY }}",
  "    GH_TOKEN: ${{ github.token }}",
  "    GH_REPO: ${{ github.repository }}",
  "    PR_NUMBER: ${{ github.event.pull_request.number }}",
  "  shell: bash",
  "  run: |",
  "    set -euo pipefail",
  '    if [[ "${FI_EVALUATION_OUTCOME:-}" != "success" ]]; then',
  '      echo "Evaluation/upload did not succeed; no comment posted." >&2',
  "      exit 1",
  "    fi",
  '    : "${FI_RUN_ID:?Evaluation/upload must output run_id for this execution}"',
  '    if [[ ! "$FI_RUN_ID" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]]; then',
  '      echo "Evaluation/upload returned an invalid run ID." >&2',
  "      exit 1",
  "    fi",
  "    shopt -s nocasematch",
  '    if [[ "$FI_RUN_ID" == "30000000-0000-4000-8000-000000000003" ]]; then',
  '      echo "Candidate must differ from the baseline run." >&2',
  "      exit 1",
  "    fi",
  '    : "${FI_API_URL:?Set repository variable FI_API_URL}"',
  '    : "${FI_APP_URL:?Set repository variable FI_APP_URL}"',
  '    : "${FI_API_KEY:?Set repository secret FI_API_KEY}"',
  '    : "${FI_SECRET_KEY:?Set repository secret FI_SECRET_KEY}"',
  '    response="$(mktemp)"',
  '    body="$(mktemp)"',
  '    trap \'rm -f "$response" "$body"\' EXIT',
  "    curl --fail --silent --show-error --max-time 60 --proto '=https' --get \\",
  '      "${FI_API_URL%/}/model-hub/experiments/v2/10000000-0000-4000-8000-000000000001/ci-summary/comment/" \\',
  '      -H "X-Api-Key: $FI_API_KEY" \\',
  '      -H "X-Secret-Key: $FI_SECRET_KEY" \\',
  '      --data-urlencode "run_id=$FI_RUN_ID" \\',
  '      --data-urlencode "baseline_run_id=30000000-0000-4000-8000-000000000003" \\',
  '      --data-urlencode "max_regressed=0" \\',
  '      --data-urlencode "top_n=5" \\',
  '      --output "$response"',
  '    jq -er --arg app "${FI_APP_URL%/}" \'',
  '      . as $summary | .body | split("](" + $summary.results_path + ")")',
  '      | join("](" + $app + $summary.results_path + ")")',
  '    \' "$response" > "$body"',
  '    gh pr comment "$PR_NUMBER" --body-file "$body"',
  "    jq -e '.passed == true' \"$response\" > /dev/null",
  "",
].join("\n");

describe("evaluation PR comment step", () => {
  it("pins the full step, posts before failing and makes the Results link absolute", () => {
    expect(ciCommentStep(ids)).toBe(expected);
  });

  it("only emits secret references even if callers supply credential values", () => {
    const step = ciCommentStep({
      ...ids,
      apiKey: "never-inline-this-api-fixture",
      secretKey: "never-inline-this-secret-fixture",
      token: "never-inline-this-token-fixture",
    });
    expect(step).toBe(expected);
    expect(step).not.toContain("never-inline");
    expect(step).not.toContain("pull_request_target");
  });

  it("binds the fixed baseline, threshold and row limit", () => {
    expect(
      ciCommentStep({
        ...ids,
        baselineRunId: "40000000-0000-4000-8000-000000000004",
        maxRegressed: 2,
        topN: 20,
      }),
    ).toBe(
      expected
        .replaceAll(ids.baselineRunId, "40000000-0000-4000-8000-000000000004")
        .replace("max_regressed=0", "max_regressed=2")
        .replace("top_n=5", "top_n=20"),
    );
  });

  it("never freezes a historical candidate supplied by a browser caller", () => {
    for (const runId of [
      "20000000-0000-4000-8000-000000000002",
      "40000000-0000-4000-8000-000000000004",
    ]) {
      expect(ciCommentStep({ ...ids, runId })).toBe(expected);
      expect(ciCommentStep({ ...ids, runId })).not.toContain(runId);
    }
  });

  it.each([
    { experimentId: "" },
    { experimentId: "not-an-experiment" },
    { baselineRunId: "" },
    { baselineRunId: '"\nextra-step' },
  ])("refuses missing or invalid run identities: %j", (extra) => {
    expect(() => ciCommentStep({ ...ids, ...extra })).toThrow(
      "Choose an experiment and a baseline run.",
    );
  });

  it.each([
    { maxRegressed: -1 },
    { maxRegressed: 0.5 },
    { maxRegressed: Infinity },
    { maxRegressed: "2" },
    { topN: 0 },
    { topN: 21 },
    { topN: 1.5 },
  ])("refuses invalid counts: %j", (extra) => {
    expect(() => ciCommentStep({ ...ids, ...extra })).toThrow(
      "Use a non-negative threshold and 1–20 regressed rows.",
    );
  });
});
