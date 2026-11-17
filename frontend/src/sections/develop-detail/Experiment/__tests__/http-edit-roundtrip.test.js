// SRC-S1b-HTTP-FE-01: Edit of a saved HTTP source, through the same pure path
// the drawer runs (hydrate -> auto-map -> experimentBodyOfValues ->
// withHttpSourceSettings -> serverExperimentBody with the edit summary, and
// the edit confirm's editChangeSummary). An HTTP Edit only saves (the server
// sends nothing); only HTTP entries travel. Authored RED against e0d9585e: an
// unchanged source was re-normalized (secret alias, empty constant), prompt
// runs travelled with it, and the confirm compared only URL and method, so a
// body, output path or header edit read as "Kept".
import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import {
  httpEditChanges,
  hydrateHttpSourceValues,
  serverExperimentBody,
  withHttpSourceSettings,
} from "../serverBody";
import { experimentBodyOfValues } from "../experimentBody";
import {
  editChangeSummary,
  editRunScope,
  emptyExperimentValues,
} from "../experimentFormValues";
import { getExperimentDefaultValue } from "../utils";
import { flattenRuns } from "../runPlan";
import { autoMapVariables } from "../variableMapping";
import {
  HTTP_RESEND_COPY,
  httpEditSummaryText,
} from "src/sections/common/Runs/httpResendCopy";

afterEach(() => setLaunchOverrideForTest(null));

const SOURCE = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
const QUESTION = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e52";
const CONTEXT = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e53";
const SPLIT = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e54";
const DATASET = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e55";
const columns = [
  { id: QUESTION, name: "question" },
  { id: CONTEXT, name: "context" },
];
const savedHttp = {
  url: "https://orders.example/v1/{{question}}",
  method: "PUT",
  headers: [
    { name: "Authorization", secret_ref: "ORDERS_TOKEN" },
    { name: "X-Team", value: "support" },
  ],
  body_template: '{"q":"{{question}}","note":"{{fixed}}","ctx":"{{context}}"}',
  output_path: '$.data["reply"]',
  streaming: false,
  audio: "text",
  timeout_seconds: 12,
};
const savedMapping = {
  question: QUESTION,
  fixed: { kind: "constant", value: "" },
  context: `${CONTEXT}::$.summary`,
};
const detail = {
  id: "experiment-1",
  name: "Orders eval",
  experiment_type: "llm",
  run_scope: { split_ids: [SPLIT] },
  prompt_configs: [],
  agent_configs: [],
  http_configs: [{
    id: SOURCE, order: 0, target_type: "http", name: "Orders API",
    http: savedHttp, variable_mapping: savedMapping,
  }],
};

// RunExperiment's Edit initial values, then its same-name auto-mapping effect.
function editValues() {
  const values = hydrateHttpSourceValues({
    ...emptyExperimentValues(),
    ...getExperimentDefaultValue(detail, []),
  }, detail, { isEditing: true });
  values.promptConfig.forEach((card) => {
    card.variableMapping = autoMapVariables(card.variables, columns, card.variableMapping);
  });
  return values;
}

const summaryOf = (original, current) =>
  editChangeSummary(original, current, { runs: [], flatten: flattenRuns, columns });

// RunExperiment's serverCtx for an HTTP Edit, and its wire body.
function saveWire(values, original = editValues()) {
  const initial = { name: original.name, description: original.description };
  const ctx = {
    isEditing: true,
    columns,
    initial,
    httpEdit: httpEditChanges(original, values, { datasetId: DATASET, columns, initial }),
  };
  return serverExperimentBody(
    withHttpSourceSettings(
      experimentBodyOfValues(values, { isEditing: true, datasetId: DATASET }),
      values.promptConfig,
      ctx,
    ),
    ctx,
  );
}

describe("Edit hydrates every saved HTTP setting", () => {
  it("restores URL, method, header secret refs, body, mapping, output path, timeout and split scope", () => {
    const before = JSON.stringify(detail);
    const values = editValues();
    expect(values.splitIds).toEqual([SPLIT]);
    expect(values.promptConfig).toHaveLength(1);
    const [card] = values.promptConfig;
    expect(card).toMatchObject({
      targetType: "http",
      name: "Orders API",
      runId: SOURCE,
      http: {
        url: savedHttp.url,
        method: "PUT",
        headers: [
          { name: "Authorization", secret: "ORDERS_TOKEN" },
          { name: "X-Team", value: "support" },
        ],
        bodyTemplate: savedHttp.body_template,
        outputPath: savedHttp.output_path,
        timeout_seconds: 12,
      },
      variableMapping: {
        question: QUESTION,
        fixed: { kind: "empty" },
        context: `${CONTEXT}::$.summary`,
      },
    });
    expect(card.variables).toEqual(["question", "fixed", "context"]);
    expect(JSON.stringify(detail)).toBe(before);
  });
});

describe("Edit saves the HTTP source back under its own identity", () => {
  it("sends an unchanged source exactly as it was read back", () => {
    const wire = saveWire(editValues());
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config).toEqual([{
      target_type: "http",
      id: SOURCE,
      name: "Orders API",
      http: savedHttp,
      variable_mapping: savedMapping,
    }]);
    // Never a prompt fallback.
    expect(wire.body.prompt_config[0]).not.toHaveProperty("prompt_id");
    expect(wire.body.prompt_config[0]).not.toHaveProperty("model");
    expect(wire.body).not.toHaveProperty("split_ids");
  });

  it("sends the edited settings, keeping the id, timeout and the other headers", () => {
    const values = editValues();
    const card = values.promptConfig[0];
    card.http = {
      ...card.http,
      outputPath: "$.answer",
      headers: [
        { name: "Authorization", secret: "ORDERS_TOKEN_V2" },
        { name: "X-Team", value: "support" },
      ],
    };
    const wire = saveWire(values);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config[0]).toEqual({
      target_type: "http",
      id: SOURCE,
      name: "Orders API",
      http: {
        url: savedHttp.url,
        method: "PUT",
        headers: [
          { name: "Authorization", secret: "ORDERS_TOKEN_V2" },
          { name: "X-Team", value: "support" },
        ],
        body_template: savedHttp.body_template,
        output_path: "$.answer",
        streaming: false,
        audio: "text",
        timeout_seconds: 12,
      },
      variable_mapping: {
        question: QUESTION,
        fixed: { kind: "empty" },
        context: `${CONTEXT}::$.summary`,
      },
    });
  });

  it("renames without touching the saved settings", () => {
    const values = editValues();
    values.promptConfig[0].name = "Orders API v2";
    const entry = saveWire(values).body.prompt_config[0];
    expect(entry).toMatchObject({ id: SOURCE, name: "Orders API v2", http: savedHttp });
    expect(entry.variable_mapping).toEqual(savedMapping);
  });
});

describe("the edit confirm names what an HTTP save changes", () => {
  it("keeps an unchanged source, by its name, and prices nothing", () => {
    const original = editValues();
    const s = summaryOf(original, editValues());
    expect(s.rerun).toEqual([]);
    expect(s.kept).toEqual(["Orders API"]);
    expect(s.httpChanged).toEqual([]);
    expect(editRunScope(original, editValues(), {
      runs: [], flatten: flattenRuns, columns,
    }).promptConfig).toEqual([]);
  });

  it.each([
    ["output path", (card) => { card.http = { ...card.http, outputPath: "$.answer" }; }, /^output path /],
    ["body", (card) => { card.http = { ...card.http, bodyTemplate: '{"q":"{{question}}"}' }; }, /^body changed$/],
    ["header secret", (card) => {
      card.http = { ...card.http, headers: [
        { name: "Authorization", secret: "ORDERS_TOKEN_V2" },
        { name: "X-Team", value: "support" },
      ] };
    }, /^header Authorization changed$/],
    ["mapping", (card) => { card.variableMapping = { ...card.variableMapping, context: CONTEXT }; }, /^mapping /],
  ])("names a %s change, and the save sends nothing", (_label, edit, word) => {
    const original = editValues();
    const current = editValues();
    edit(current.promptConfig[0]);
    const s = summaryOf(original, current);
    expect(s.rerun).toEqual(["Orders API"]);
    expect(s.kept).toEqual([]);
    expect(s.changes["Orders API"].some((w) => word.test(w))).toBe(true);
    expect(s.httpChanged).toEqual(["Orders API"]);
    expect(s.text).not.toContain(SOURCE);
    // The confirm says Changed, never Re-run, and that saving sends nothing.
    expect(httpEditSummaryText(s.text)).toMatch(/^Changed: Orders API \(/);
    expect(HTTP_RESEND_COPY.editSaveOnly).toMatch(/^Saving sends no requests to the endpoint/);
    // A changed HTTP source alone never blocks Save.
    const wire = saveWire(current, original);
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config.map((e) => e.id)).toEqual([SOURCE]);
  });

  it("SRC-S1b-HTTP-FE-09 points to the Retry actions that ask first, never to a one-click rerun", () => {
    // Rerun Experiment re-sends every HTTP row with one click and no warning.
    expect(HTTP_RESEND_COPY.editSaveOnly).toBe(
      "Saving sends no requests to the endpoint and keeps the existing results. Evaluations you add or change score a row only when it is sent again, so existing scores stay as they are. To send requests with the new settings, use Retry failed rows on the Data tab, or the retry button on a failed cell. Each asks before it sends.",
    );
    // An evaluation added or changed in Edit does not re-score existing rows.
    expect(HTTP_RESEND_COPY.editSaveOnly).toContain(
      "Evaluations you add or change score a row only when it is sent again",
    );
    expect(HTTP_RESEND_COPY.editSaveOnly).not.toMatch(/Rerun Experiment/i);
    expect(HTTP_RESEND_COPY.editSaveOnly).toContain(HTTP_RESEND_COPY.failedRowsAction);
  });
});

describe("Edit of an experiment with HTTP and prompt runs", () => {
  const PROMPT = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e56";
  const VERSION = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e57";
  const mixed = {
    ...detail,
    prompt_configs: [{
      id: "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e58", order: 0, name: "Prompt run",
      model: "model-a", prompt_id: PROMPT, prompt_version: VERSION, prompt_name: "Support",
      model_params: {}, output_format: "string",
    }],
    http_configs: [{ ...detail.http_configs[0], order: 1 }],
  };
  const mixedValues = () => {
    const values = hydrateHttpSourceValues({
      ...emptyExperimentValues(),
      ...getExperimentDefaultValue(mixed, []),
    }, mixed, { isEditing: true });
    values.promptConfig.forEach((card) => {
      if (card.targetType === "http")
        card.variableMapping = autoMapVariables(card.variables, columns, card.variableMapping);
    });
    return values;
  };
  const httpCardOf = (values) => values.promptConfig.find((c) => c.targetType === "http");

  it("sends only the HTTP source; the prompt run stays as saved", () => {
    const values = mixedValues();
    const wire = saveWire(values, mixedValues());
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config).toEqual([{
      target_type: "http", id: SOURCE, name: "Orders API",
      http: savedHttp, variable_mapping: savedMapping,
    }]);
  });

  it.each([
    ["adds an HTTP run", (values) => values.promptConfig.push({
      ...httpCardOf(values), _itemId: "new", runId: undefined, httpStored: undefined, ranWith: undefined,
    })],
    ["removes the HTTP run", (values) => {
      values.promptConfig = values.promptConfig.filter((c) => c.targetType !== "http");
    }],
    ["changes the prompt run", (values) => {
      const card = values.promptConfig.find((c) => c.targetType !== "http");
      card.promptVersion = PROMPT;
    }],
  ])("blocks Save with the reason when it %s", (_label, edit) => {
    const original = mixedValues();
    const values = mixedValues();
    edit(values);
    const wire = saveWire(values, original);
    const block = wire.blocks.find((b) => b.text === HTTP_RESEND_COPY.editHttpOnly);
    expect(block).toBeTruthy();
    expect(block.step).toBe(0);
    expect(wire.body.prompt_config.every((e) => e.target_type === "http")).toBe(true);
  });
});
