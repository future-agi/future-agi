import { describe, expect, it } from "vitest";
import { catalogEntry } from "../../catalog";
import {
  API_KEY_NOTE,
  captureOf,
  changedFields,
  commandPreview,
  defaultRecipe,
  environmentMcp,
  moreSettingsSummary,
  permissionTooltip,
  wideningNotice,
} from "../recipeModel";
import { harnessScript, steps } from "../../snippets/harness";
import { longestLine } from "../../snippets/lines";

const ENV = {
  image: "ghcr.io/acme/acme-support-sandbox:1.4",
  resources: { cpu: 2, memory_gb: 4, timeout_s: 600 },
  mcp: {
    name: "acme-support-sandbox",
    command: "acme-sandbox mcp --stdio",
    tools: 12,
    tool_names: [],
  },
};
const CC = catalogEntry("claude_code");
const ALICE = {
  name: "alice@mbp-14",
  owner: { id: "u_alice", name: "Alice Park" },
  you: false,
  allow_recipe_changes: false,
};

describe("recipeModel", () => {
  it("defaults come from the task suite's Environment", () => {
    const r = defaultRecipe(CC, ENV);
    expect(r.model).toBe("claude-sonnet-4-5");
    expect(r.permissions).toBe("edit");
    expect(r.mcp_servers).toHaveLength(1);
    expect(environmentMcp(ENV)).toMatchObject({
      name: "acme-support",
      transport: "stdio",
      tools: 12,
      from: "environment",
    });
    expect(defaultRecipe(CC, null).mcp_servers).toEqual([]);
  });

  it("prints the command preview with the documented Claude Code flags", () => {
    const r = defaultRecipe(CC, ENV);
    expect(commandPreview(r).text).toBe(
      'claude -p "<task>" --model claude-sonnet-4-5 --output-format stream-json --verbose --permission-mode acceptEdits --mcp-config .fi/mcp.json --allowedTools "Bash,Edit,Write,mcp__acme-support" --disallowedTools "WebFetch,WebSearch" --max-turns 40',
    );
    expect(commandPreview(r).approximate).toBe(false);
    const aider = defaultRecipe(catalogEntry("aider"), null);
    expect(commandPreview(aider).approximate).toBe(true);
    expect(commandPreview(aider).label).toBe(
      "Flags from the Aider docs (headless mode)",
    );
  });

  it("gives each permission its CLI flag", () => {
    expect(permissionTooltip("claude_code", "edit")).toBe(
      "Edit files → --permission-mode acceptEdits",
    );
    // OpenHands has no headless permission flag: the wrapper enforces it.
    expect(permissionTooltip("openhands", "full_auto")).toBe(
      "Full auto → --permissions full_auto",
    );
    expect(permissionTooltip("goose", "read_only")).toBe(
      "Read only → GOOSE_MODE=chat",
    );
  });

  it("summarises More settings in one line", () => {
    expect(moreSettingsSummary(defaultRecipe(CC, ENV))).toBe(
      "v2.x · Shell, Edit files · CLAUDE.md · 40 turns · $1.00 per try · Git diff + final message",
    );
  });

  it("labels capture from the catalog", () => {
    expect(captureOf(CC).gateway.label).toBe("Full steps (harness transcript)");
    expect(captureOf("codex_cli").gateway.label).toBe(
      "Full steps (harness transcript)",
    );
    // options-truth L2 G6: OpenHands' trajectory and Aider's history are
    // step records; a custom command has only what it writes itself.
    expect(captureOf("openhands").gateway.label).toBe(
      "Full steps (harness transcript)",
    );
    expect(captureOf("aider").upload.label).toBe(
      "Full steps (harness transcript)",
    );
    expect(captureOf("custom_command").upload.label).toBe(
      "Final result only (no transcript)",
    );
    expect(API_KEY_NOTE).toBe(
      "Needs an API key from Workspace secrets: a subscription login can't be redirected through the gateway.",
    );
  });

  it("asks the owner to confirm a widening recipe on their machine", () => {
    const base = defaultRecipe(CC, ENV);
    expect(wideningNotice(base, base, ALICE).confirm).toBe(false);
    const full = { ...base, permissions: "full_auto" };
    expect(wideningNotice(full, base, ALICE).line).toBe(
      "Alice Park will be asked to confirm on alice@mbp-14 before the run starts.",
    );
    const mcp = {
      ...base,
      mcp_servers: [...base.mcp_servers, { name: "github" }],
    };
    expect(wideningNotice(mcp, base, ALICE).reasons).toContain("mcp_server");
    // your own machine started with --allow-recipe-changes: no confirmation
    const mine = { ...ALICE, you: true, allow_recipe_changes: true };
    expect(wideningNotice(full, base, mine).confirm).toBe(false);
    expect(wideningNotice(full, base, null).confirm).toBe(false);
  });

  it("lists only changed fields (compact rerun)", () => {
    const base = defaultRecipe(CC, ENV);
    expect(changedFields(base, base)).toEqual([]);
    expect(changedFields({ ...base, permissions: "full_auto" }, base)).toEqual([
      { field: "Permissions", from: "Edit files", to: "Full auto" },
    ]);
  });
});

describe("snippets/harness", () => {
  const ids = {
    datasetId: "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06",
    datasetName: "Support ops tasks · sandbox",
    experimentId: "e6000000-0000-4000-8000-000000000002",
    runId: "a6000000-0000-4000-8000-00000000002a",
    runLabel: "#2A",
    inputColumn: "instruction",
  };

  it.each(["claude_code", "codex_cli", "aider", "custom_command", "openhands"])(
    "%s: a complete script, every line ≤ 88 characters",
    (id) => {
      const entry = catalogEntry(id);
      const file = harnessScript({
        entry,
        ids,
        recipe: defaultRecipe(entry, ENV),
      });
      expect(file.startsWith("# TRACE + UPLOAD:")).toBe(true);
      expect(longestLine(file)).toBeLessThanOrEqual(88);
      [
        "# 1. fetch_rows(): the dataset's rows",
        "# 2. fresh_folder(row, trial): a working copy per try",
        `# 3. run_cli(task, folder): the CLI headless (flags from the ${entry.name} docs)`,
        "# 4. Spans with the baton:",
        "# 5. collect(folder): only the artifacts in COLLECT",
        "# 6. upload(results): POST …/runs/{run_id}/results/",
      ].forEach((part) => expect(file).toContain(part));
      expect(file).toContain(`RUN_ID = "${ids.runId}"`);
      expect(file).not.toMatch(/Works today|Preview|0\.9|coming|not released/i);
    },
  );

  it("labels the script's capture per harness", () => {
    // CAPTURE = "…" on one line, or ("…" "…") split past 88 characters
    const cap = (id) => {
      const file = harnessScript({ entry: catalogEntry(id), ids });
      const m = file.match(/^CAPTURE = (?:"(.*)"|\(\n((?: {4}".*"\n)+)\))$/m);
      return m[1] ?? [...m[2].matchAll(/"(.*)"/g)].map((x) => x[1]).join("");
    };
    // one wording (RECORD_WORDS) per harness across the chip, the sentence
    // and the script, whatever the mode (options-truth L2 G6)
    const steps = "every message and tool call, as a trace";
    ["claude_code", "codex_cli", "gemini_cli", "goose", "openhands"].forEach(
      (id) => expect(cap(id)).toBe(steps),
    );
    expect(cap("mini_swe_agent")).toBe(steps);
    expect(cap("aider")).toBe("every model call and each file it edits");
    expect(cap("custom_command")).toBe(
      "your trace file (FI_TRACE_FILE) if the command writes one, else the final output",
    );
  });

  it("reuses lane Ra's fetch_rows code verbatim", () => {
    const file = harnessScript({ entry: CC, ids });
    expect(file).toContain(
      '    url = f"{FI_BASE_URL}/model-hub/develops/{DATASET_ID}/get-dataset-table/"',
    );
    expect(file).toContain('def read_sample_rows(path="sample_rows.jsonl"):');
  });

  // dataset integration FE-0: no live connection and no canned CLI stand-in.
  it("steps(): the file and its run line; form=connect prints the same", () => {
    const up = steps({ entry: CC, form: "upload", ids });
    expect(up.find((s) => s.id === "file").code).toBe(
      harnessScript({ entry: CC, ids }),
    );
    expect(up.map((s) => s.id)).not.toContain("fake");
    const live = steps({
      entry: CC,
      form: "connect",
      ids: { token: "fic_test_placeholder" },
    });
    expect(live.map((s) => s.code).join("\n")).not.toMatch(
      /futureagi connect|fake_cli/,
    );
  });
});
