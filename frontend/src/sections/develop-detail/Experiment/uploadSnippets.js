// Upload snippets for a From code run (PRODUCT-SPEC §3.9, critique N5;
// options-truth L1 G2). options-truth L5 G5: no screen prints them while the
// upload route is not real (owner rule 3); the builders stay for the SDK. Every snippet is a complete file: it reads
// sample_rows.jsonl, defines my_agent, and posts to the run's upload
// endpoint (POST /model-hub/experiments/v2/{experiment_id}/runs/{run_id}/
// results/) with the run's ids; keys come from FI_API_KEY / FI_SECRET_KEY.
import {
  CI_PATH,
  RUNNER_FILES,
} from "src/sections/common/RunSources/snippets/lines";
import { FI_BASE_URL } from "src/sections/common/commands/requestSnippets";

// The one command builder owns the API host; re-exported under the same name.
export { FI_BASE_URL };
export const SNIPPET_TABS = [
  // IT-R5 G1 S2: Python first.
  { value: "python", label: "Python", language: "python" },
  // IT-R1 G1 P11: a TypeScript run gets a TypeScript tab.
  { value: "typescript", label: "TypeScript", language: "typescript" },
  { value: "curl", label: "cURL", language: "bash" },
  { value: "github", label: "GitHub Action", language: "yaml" },
];

/** A realistic reply for an example (the sample body; pure). */
function replyFor(text) {
  const t = String(text ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (!t)
    return "Thanks for reaching out. I've checked your account and sorted it.";
  const topic = t.length > 70 ? `${t.slice(0, 67)}…` : t;
  return `Thanks for the details ("${topic}"). I've checked your order and fixed it; you'll get a confirmation email shortly.`;
}

/**
 * IT-R5 G1 S2: `results.json`, the upload body for every given example
 * (one entry per example and trial), to see the shape or to post with
 * `--data @results.json`. `examples` ([{id, text}]) give each a reply.
 */
export function sampleResultsJson({
  exampleIds = [],
  examples = [],
  trials = 1,
} = {}) {
  const list = examples.length
    ? examples
    : exampleIds.map((id) => ({ id, text: "" }));
  const t = Math.max(1, Number(trials) || 1);
  const results = list.flatMap((ex, i) =>
    Array.from({ length: t }, (_, k) => ({
      example_id: ex.id,
      trial: k + 1,
      output: replyFor(ex.text),
      latency_ms: 1400 + ((i * 7 + k * 3) % 9) * 110,
      error: null,
    })),
  );
  return `${JSON.stringify({ results, complete: true }, null, 2)}\n`;
}

/** The row fields my_agent tries, in order, when the input column is unknown. */
export const INPUT_COLUMNS = Object.freeze([
  "instruction",
  "customer_message",
  "user_message",
  "message",
  "input",
  "question",
  "query",
  "prompt",
]);

export const uploadPath = (experimentId, runId) =>
  `/model-hub/experiments/v2/${experimentId}/runs/${runId}/results/`;

export const UPLOAD_FILES = Object.freeze({
  python: RUNNER_FILES.upload_py,
  typescript: RUNNER_FILES.upload_ts,
});

/**
 * Self-contained upload scripts (S3): read sample_rows.jsonl, run
 * `my_agent` on every row, post the results with this run's ids (the
 * reserved ones before create), `complete: true` only on the last batch.
 * Without ids the scripts read FI_EXPERIMENT_ID / FI_RUN_ID.
 * @param {object} p
 * @param {string} [p.experimentId]
 * @param {string} [p.runId]
 * @param {string} [p.runLabel]   `#6A`
 * @param {string} [p.exampleId]  a real example (row) id for the cURL body
 * @param {string} [p.inputColumn]  the row field my_agent reads
 * @param {number} [p.trials]
 * @returns {{curl: string, python: string, typescript: string, github: string}}
 */
export function buildUploadSnippets({
  experimentId,
  runId,
  runLabel,
  exampleId,
  inputColumn,
  trials = 1,
} = {}) {
  const t = Math.max(1, Number(trials) || 1);
  // S3: a run is its UUID (the label only when no id is reserved yet).
  const label = runId ? ` ${runId}` : runLabel ? ` "${runLabel}"` : "";
  const expPy = experimentId
    ? JSON.stringify(experimentId)
    : 'os.environ["FI_EXPERIMENT_ID"]';
  const runPy = runId ? JSON.stringify(runId) : 'os.environ["FI_RUN_ID"]';
  const expTs = experimentId
    ? JSON.stringify(experimentId)
    : 'process.env.FI_EXPERIMENT_ID ?? ""';
  const runTs = runId ? JSON.stringify(runId) : 'process.env.FI_RUN_ID ?? ""';
  // N6: my_agent reads the dataset's input column (a task suite's
  // `instruction`); without one, the first input-like column the row has.
  const col = inputColumn ? JSON.stringify(inputColumn) : null;
  const inputList = INPUT_COLUMNS.map(
    (c) => `\n    ${JSON.stringify(c)},`,
  ).join("");
  const pyText = col
    ? `    text = str(row.get(${col}) or "")`
    : `    text = str(next((row[c] for c in INPUT_COLUMNS if row.get(c)), ""))`;
  const pyInputs = col ? "" : `INPUT_COLUMNS = (${inputList}\n)\n\n\n`;
  const tsText = col
    ? `String(row[${col}] ?? "")`
    : `String(INPUT_COLUMNS.map((c) => row[c]).find((v) => v) ?? "")`;
  const tsInputs = col
    ? ""
    : `const INPUT_COLUMNS = [${inputList.replace(/,$/, "")}\n];\n\n`;
  const py = UPLOAD_FILES.python;
  const ts = UPLOAD_FILES.typescript;

  const curlId = exampleId ?? "$EXAMPLE_ID";
  const curl = `# Upload one result for run${label}${exampleId ? "" : " (EXAMPLE_ID: one of the run's row ids)"}.
curl -X POST "${FI_BASE_URL}${uploadPath(experimentId ?? "$FI_EXPERIMENT_ID", runId ?? "$FI_RUN_ID")}" \\
  -H "X-Api-Key: $FI_API_KEY" \\
  -H "X-Secret-Key: $FI_SECRET_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "results": [
      {
        "example_id": "${curlId}",
        "trial": 1,
        "output": "Sorry about the double charge. I have refunded it.",
        "latency_ms": 1840,
        "error": null
      }
    ],
    "complete": false
  }'`;

  const python = `# ${py} · run my_agent on every row of sample_rows.jsonl and upload the
# results to run${label} (POST /model-hub/experiments/v2/EXPERIMENT_ID/runs/RUN_ID/results/).
#   python ${py} --dry-run          prints every batch, sends nothing, needs no keys
#   python ${py}                    uploads in batches of 50
#   python ${py} --trials ${t} --fail-under 0.8
import argparse
import json
import os
import sys
import time

FI_BASE_URL = os.environ.get("FI_BASE_URL", "${FI_BASE_URL}")
EXPERIMENT_ID = ${expPy}
RUN_ID = ${runPy}
BATCH_SIZE = 50


${pyInputs}def my_agent(row):
    """Your code: one row in, the reply out. Replace the body with your agent."""
${pyText}
    return f"Thanks, I've looked into it: {text[:80]}"


def read_rows(path="sample_rows.jsonl"):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="Run and upload results.")
    parser.add_argument("--rows-file", default="sample_rows.jsonl")
    parser.add_argument("--run", default=RUN_ID)
    parser.add_argument("--trials", type=int, default=${t})
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-under", type=float, default=None)
    args = parser.parse_args()
    rows = read_rows(args.rows_file)[: args.limit]
    url = (
        f"{FI_BASE_URL}/model-hub/experiments/v2/{EXPERIMENT_ID}"
        f"/runs/{args.run}/results/"
    )
    results = []
    for row in rows:
        for trial in range(1, args.trials + 1):
            started = time.time()
            try:
                output, error = my_agent(row), None
            except Exception as exc:  # recorded on the row; the run goes on
                output, error = None, f"{type(exc).__name__}: {exc}"
            results.append({
                "example_id": row["id"],
                "trial": trial,
                "output": output,
                "error": error,
                "latency_ms": int((time.time() - started) * 1000),
            })
    batches = [results[i : i + BATCH_SIZE] for i in range(0, len(results), BATCH_SIZE)]
    for n, batch in enumerate(batches or [[]], start=1):
        body = {"results": batch, "complete": n == max(1, len(batches))}
        if args.dry_run:
            print(f"[dry-run] POST {url}")
            print(json.dumps(body, indent=2))
            continue
        import requests  # only a real upload needs it

        headers = {
            "X-Api-Key": os.environ["FI_API_KEY"],
            "X-Secret-Key": os.environ["FI_SECRET_KEY"],
        }
        response = requests.post(url, headers=headers, json=body, timeout=60)
        response.raise_for_status()
        print(f"uploaded batch {n} of {len(batches)}")
    ok = sum(1 for r in results if not r["error"])
    rate = ok / len(results) if results else 0.0
    print(f"pass rate {rate:.2f} ({ok} of {len(results)} tries without an error)")
    if args.fail_under is not None and rate < args.fail_under:
        sys.exit(f"pass rate {rate:.2f} is below --fail-under {args.fail_under}")


if __name__ == "__main__":
    main()
`;

  const github = `# ${CI_PATH} · runs ${py} on every pull request and uploads to run${label}
name: fi-experiment
on:
  pull_request:
  workflow_dispatch:
jobs:
  experiment:
    runs-on: ubuntu-latest
    env:
      FI_API_KEY: \${{ secrets.FI_API_KEY }}
      FI_SECRET_KEY: \${{ secrets.FI_SECRET_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install requests
      - name: Run every row and upload the results
        run: python ${py} --run \${{ vars.FI_RUN_ID }} --trials ${t} --fail-under 0.85`;

  const typescript = `// ${ts} · run myAgent on every row of sample_rows.jsonl and upload the
// results to run${label}.
//   npx tsx ${ts} --dry-run   prints every batch, sends nothing, needs no keys
//   npx tsx ${ts}             uploads in batches of 50
import { readFileSync } from "node:fs";

const FI_BASE_URL = process.env.FI_BASE_URL ?? "${FI_BASE_URL}";
const EXPERIMENT_ID = ${expTs};
const RUN_ID = ${runTs};
const argv = process.argv.slice(2);
const DRY_RUN = argv.includes("--dry-run");
const TRIALS = Number(argv[argv.indexOf("--trials") + 1]) || ${t};

type Row = { id: string; [column: string]: unknown };

${tsInputs}// Your code: one row in, the reply out. Replace the body with your agent.
async function myAgent(row: Row): Promise<string> {
  return \`Thanks, I've looked into it: \${${tsText}.slice(0, 80)}\`;
}

async function main() {
  const rows: Row[] = readFileSync("sample_rows.jsonl", "utf8")
    .split("\\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
  const url = \`\${FI_BASE_URL}/model-hub/experiments/v2/\${EXPERIMENT_ID}/runs/\${RUN_ID}/results/\`;
  const results: object[] = [];
  for (const row of rows)
    for (let trial = 1; trial <= TRIALS; trial += 1) {
      const started = Date.now();
      let output: string | null = null;
      let error: string | null = null;
      try {
        output = await myAgent(row);
      } catch (err) {
        error = String(err);
      }
      results.push({ example_id: row.id, trial, output, error, latency_ms: Date.now() - started });
    }
  const batches: object[][] = [];
  for (let i = 0; i < results.length; i += 50) batches.push(results.slice(i, i + 50));
  if (!batches.length) batches.push([]);
  for (const [i, batch] of batches.entries()) {
    const body = { results: batch, complete: i === batches.length - 1 };
    if (DRY_RUN) {
      console.log(\`[dry-run] POST \${url}\`);
      console.log(JSON.stringify(body, null, 2));
      continue;
    }
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "X-Api-Key": process.env.FI_API_KEY ?? "",
        "X-Secret-Key": process.env.FI_SECRET_KEY ?? "",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(\`upload: \${resp.status}\`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
`;

  return { curl, python, typescript, github };
}

/**
 * CR#5: what to run before the snippet, per tab: `env` (keys) and
 * `install` (null when nothing to install).
 */
export function snippetSetup(tab, { runId } = {}) {
  if (tab === "github")
    return {
      env: `# Repository secrets (run once, or Settings › Secrets and variables › Actions)
gh secret set FI_API_KEY
gh secret set FI_SECRET_KEY
gh variable set FI_RUN_ID --body ${runId || '"<this run\'s id>"'}`,
      install: null,
    };
  return {
    env: `export FI_API_KEY="<your api key>"
export FI_SECRET_KEY="<your secret key>"`,
    install:
      tab === "python"
        ? "pip install requests"
        : tab === "typescript"
          ? "npm i -D tsx"
          : null,
    file:
      tab === "python"
        ? UPLOAD_FILES.python
        : tab === "typescript"
          ? UPLOAD_FILES.typescript
          : null,
  };
}
