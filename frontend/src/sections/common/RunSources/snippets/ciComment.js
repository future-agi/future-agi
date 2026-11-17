// Pure post-evaluation step. Only repository secret references are emitted;
// this module never reads the browser's keys or invokes either service.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function ciCommentStep({
  experimentId,
  baselineRunId,
  maxRegressed = 0,
  topN = 5,
} = {}) {
  if (![experimentId, baselineRunId].every((id) => UUID.test(id ?? "")))
    throw new Error("Choose an experiment and a baseline run.");
  if (
    !Number.isSafeInteger(maxRegressed) ||
    maxRegressed < 0 ||
    !Number.isSafeInteger(topN) ||
    topN < 1 ||
    topN > 20
  )
    throw new Error("Use a non-negative threshold and 1–20 regressed rows.");

  return [
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
    `    if [[ "$FI_RUN_ID" == "${baselineRunId}" ]]; then`,
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
    `      "\${FI_API_URL%/}/model-hub/experiments/v2/${experimentId}/ci-summary/comment/" \\`,
    '      -H "X-Api-Key: $FI_API_KEY" \\',
    '      -H "X-Secret-Key: $FI_SECRET_KEY" \\',
    '      --data-urlencode "run_id=$FI_RUN_ID" \\',
    `      --data-urlencode "baseline_run_id=${baselineRunId}" \\`,
    `      --data-urlencode "max_regressed=${maxRegressed}" \\`,
    `      --data-urlencode "top_n=${topN}" \\`,
    '      --output "$response"',
    '    jq -er --arg app "${FI_APP_URL%/}" \'',
    '      . as $summary | .body | split("](" + $summary.results_path + ")")',
    '      | join("](" + $app + $summary.results_path + ")")',
    '    \' "$response" > "$body"',
    '    gh pr comment "$PR_NUMBER" --body-file "$body"',
    "    jq -e '.passed == true' \"$response\" > /dev/null",
    "",
  ].join("\n");
}
