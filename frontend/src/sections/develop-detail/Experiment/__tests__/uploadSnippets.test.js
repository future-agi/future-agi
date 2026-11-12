import { describe, expect, it } from "vitest";
import {
  buildUploadSnippets,
  snippetSetup,
  uploadPath,
} from "../uploadSnippets";

describe("uploadSnippets", () => {
  const s = buildUploadSnippets({
    experimentId: "EXP_6",
    runId: "RUN_6A",
    runLabel: "#6A",
    exampleId: "ROW_1",
  });

  it("calls the E3 upload endpoint with the real ids", () => {
    const path = uploadPath("EXP_6", "RUN_6A");
    expect(path).toBe("/model-hub/experiments/v2/EXP_6/runs/RUN_6A/results/");
    expect(s.curl).toContain(path);
    // ot-L1 G2: the files carry the run's own ids (never `{run_id}`)
    expect(s.python).toContain('EXPERIMENT_ID = "EXP_6"');
    expect(s.python).toContain('RUN_ID = "RUN_6A"');
    expect(s.python).toContain(
      'f"{FI_BASE_URL}/model-hub/experiments/v2/{EXPERIMENT_ID}"',
    );
    expect(s.python).toContain('f"/runs/{args.run}/results/"');
    expect(s.typescript).toContain('const RUN_ID = "RUN_6A";');
    [s.curl, s.python, s.typescript, s.github].forEach((code) =>
      expect(code).not.toMatch(/\{run_id\}|\{experiment_id\}/),
    );
    expect(s.curl).toContain('"example_id": "ROW_1"');
  });

  it("reads keys from the environment or repository secrets", () => {
    expect(s.curl).toContain("$FI_API_KEY");
    expect(s.curl).toContain("$FI_SECRET_KEY");
    expect(s.python).toContain('os.environ["FI_API_KEY"]');
    expect(s.python).toContain('os.environ["FI_SECRET_KEY"]');
    expect(s.github).toContain("secrets.FI_API_KEY");
    expect(s.github).toContain(".github/workflows/fi-experiment.yml");
    expect(s.github).toContain("vars.FI_RUN_ID");
  });

  it("uses no SDK", () => {
    expect(s.python).not.toMatch(/from fi|import fi/);
    expect(s.python).toContain("import requests");
  });

  it("is self-contained: reads sample_rows.jsonl and defines my_agent", () => {
    expect(s.python).toContain('def read_rows(path="sample_rows.jsonl"):');
    expect(s.python).toContain("def my_agent(row):");
    expect(s.typescript).toContain("async function myAgent(row: Row)");
    expect(s.typescript).toContain('readFileSync("sample_rows.jsonl", "utf8")');
    // no top-level await
    expect(s.typescript).toMatch(/\nmain\(\)\.catch\(/);
  });
});

describe("uploadSnippets · CR#3 complete loop and CR#5 setup blocks", () => {
  const s = buildUploadSnippets({ experimentId: "EXP", runId: "RUN" });
  it("python loops every example, batches 50, completes on the last batch", () => {
    expect(s.python).toContain("    for row in rows:");
    expect(s.python).toContain("BATCH_SIZE = 50");
    // complete: true only on the last batch
    expect(s.python).toContain(
      'body = {"results": batch, "complete": n == max(1, len(batches))}',
    );
    expect(s.python).toContain(
      'parser.add_argument("--dry-run", action="store_true")',
    );
  });
  it("adds a trial loop only when the run has several tries", () => {
    // tries are a flag with the run's own default
    expect(s.python).toContain(
      'parser.add_argument("--trials", type=int, default=1)',
    );
    const t = buildUploadSnippets({ trials: 3 });
    expect(t.python).toContain(
      'parser.add_argument("--trials", type=int, default=3)',
    );
    expect(t.python).toContain("for trial in range(1, args.trials + 1):");
    expect(t.python).toContain('"trial": trial');
  });
  it("gives env and install blocks per tab", () => {
    expect(snippetSetup("python")).toEqual({
      env: expect.stringContaining("export FI_API_KEY"),
      install: "pip install requests",
      file: "upload_results.py",
    });
    expect(snippetSetup("typescript").file).toBe("upload_results.ts");
    expect(snippetSetup("curl").install).toBeNull();
    expect(snippetSetup("github").env).toContain("gh secret set FI_API_KEY");
    expect(snippetSetup("github", { runId: "RUN" }).env).toContain(
      "gh variable set FI_RUN_ID --body RUN",
    );
  });
});

describe("IT-R5 G1 S2 · How to upload", async () => {
  const { SNIPPET_TABS, buildUploadSnippets, sampleResultsJson } = await import(
    "../uploadSnippets"
  );
  it("Python is the first tab; GitHub is a whole workflow", () => {
    expect(SNIPPET_TABS[0].value).toBe("python");
    const g = buildUploadSnippets({ experimentId: "e1", runId: "r1" }).github;
    for (const step of [
      "actions/checkout@v4",
      "actions/setup-python@v5",
      "pip install requests",
      "python upload_results.py",
      "secrets.FI_SECRET_KEY",
    ])
      expect(g).toContain(step);
    // The gate is the script's own --fail-under (exit 1 below it).
    expect(g).toContain("--fail-under 0.85");
    expect(g).not.toMatch(/Preview|not available yet/);
  });
  it("sample results.json has one entry per example and trial", () => {
    const body = JSON.parse(
      sampleResultsJson({ exampleIds: ["a", "b"], trials: 2 }),
    );
    expect(body.results).toHaveLength(4);
    expect(body.complete).toBe(true);
    expect(body.results[3]).toMatchObject({ example_id: "b", trial: 2 });
    // every example gets a reply built from its own text
    const withText = JSON.parse(
      sampleResultsJson({
        examples: [
          { id: "a", text: "Mixer arrived cracked" },
          { id: "b", text: "Change my address" },
        ],
      }),
    );
    expect(withText.results[0].output).toContain("Mixer arrived cracked");
    expect(withText.results[1].output).toContain("Change my address");
  });
});
