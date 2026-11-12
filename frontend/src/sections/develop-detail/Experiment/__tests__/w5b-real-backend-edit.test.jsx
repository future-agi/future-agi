import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ExperimentSourceGate from "../ExperimentSourceGate";
import { cardsFromExperiment } from "../StartFromExperiment";
import { escapeModelKey, promptConfigTransform, slimModelParams } from "../utils";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";

vi.mock("../RunExperimentFormSkeleton", () => ({
  default: () => <span>Loading experiment</span>,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/r2", () => ({ useRunConfigs: vi.fn() }));
afterEach(cleanup);

// ExperimentDetailV2Serializer / ExperimentPromptConfigOutputSerializer:
// c25 has these saved configurations, not the prototype's target-shaped runs.
const prompt = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "First variant",
  prompt_id: "22222222-2222-4222-8222-222222222222",
  prompt_name: "Answer a question",
  prompt_version: "33333333-3333-4333-8333-333333333333",
  prompt_version_name: "v2",
  model: "gpt-4.1",
  model_display_name: "GPT-4.1",
  model_config: {},
  model_params: {
    providers: "openai",
    logoUrl: "",
    temperature: 0,
    max_tokens: 128,
    top_p: 0.25,
    presence_penalty: 0.5,
    frequency_penalty: 0.75,
    stop: ["END"],
    logit_bias: { "123": -100 },
    response_format: { type: "json_object" },
    reasoning: { dropdowns: { reasoningEffort: "low" } },
  },
  configuration: { toolChoice: "none", tools: [] },
  output_format: "json",
  messages: [],
  voice_input_column_id: null,
  order: 0,
};
const secondPrompt = {
  ...prompt,
  id: "44444444-4444-4444-8444-444444444444",
  name: "Second variant",
  model_params: {
    ...prompt.model_params,
    temperature: 0.8,
    max_tokens: 512,
    stop: ["DONE", "<|end|>"],
    logit_bias: { "456": 0, "789": 2.5 },
  },
  configuration: { toolChoice: "auto", tools: [] },
  output_format: "string",
  order: 1,
};
const agent = {
  id: "55555555-5555-4555-8555-555555555555",
  name: "Agent variant",
  agent_id: "66666666-6666-4666-8666-666666666666",
  agent_name: "Answer graph",
  agent_version: "77777777-7777-4777-8777-777777777777",
  agent_version_name: 2,
  order: 0,
};
const c25 = {
  id: "88888888-8888-4888-8888-888888888888",
  name: "Saved experiment",
  dataset_id: "99999999-9999-4999-8999-999999999999",
  column_id: null,
  experiment_type: "llm",
  status: "completed",
  prompt_configs: [prompt],
  agent_configs: [],
  user_eval_metrics: [],
};
// Canonical results are display projections, not complete editable configs.
const canonical = {
  run_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  experiment_id: c25.id,
  dataset_id: c25.dataset_id,
  task_kind: "prompt",
  prompt_config_id: prompt.id,
  status: "completed",
  ordinal: 1,
  config: { model: { name: prompt.model, params: { temperature: 0 } } },
};
const c26 = { ...c25, runs: [canonical], runs_total: 1, runs_has_more: false };
const unavailable = () => ({
  isError: true,
  error: { statusCode: 404 },
  refetch: vi.fn(),
});
const editValues = (detail) => cardsFromExperiment(detail, {}, { mode: "edit" });
const entriesOf = (values, isEditing = true) =>
  promptConfigTransform(values.promptConfig, "llm", values.outputFormat, isEditing);

const expectSavedPrompt = (card, saved = prompt) => {
  expect(card).toMatchObject({
    targetType: "prompt",
    promptId: saved.prompt_id,
    promptVersion: saved.prompt_version,
    configuration: saved.configuration,
    outputFormat: saved.output_format,
    model: [{ id: saved.id, promptConfigId: saved.id, value: saved.model }],
  });
  expect(card.readOnly).not.toBe(true);
  expect(card.modelParams[escapeModelKey(saved.model)].maxTokens).toBe(
    saved.model_params.max_tokens,
  );
};

// RED specifications on both pinned bases; execution belongs to the paired runner.
describe("W5-B-001 real backend edit", () => {
  it.each(["c25", "c26"])(
    "opens %s saved configurations when supplementary R1 is unavailable",
    (line) => {
      render(
        <ExperimentSourceGate detail={{ data: line === "c25" ? c25 : c26 }} configs={unavailable()}>
          <input aria-label="Edit saved experiment" />
        </ExperimentSourceGate>,
      );
      expect(screen.getByLabelText("Edit saved experiment")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("opens saved configurations without waiting for supplementary R1", () => {
    render(
      <ExperimentSourceGate detail={{ data: c25 }} configs={{ isFetching: true }}>
        <input aria-label="Edit saved experiment" />
      </ExperimentSourceGate>,
    );
    expect(screen.getByLabelText("Edit saved experiment")).toBeInTheDocument();
    expect(screen.queryByText("Loading experiment")).not.toBeInTheDocument();
  });

  it("blocks an unavailable authoritative detail and retries that read", () => {
    const detail = unavailable();
    render(
      <ExperimentSourceGate detail={detail} configs={{ data: { results: [canonical] } }}>
        <input aria-label="Edit saved experiment" />
      </ExperimentSourceGate>,
    );
    expect(screen.queryByLabelText("Edit saved experiment")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Experiment unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(detail.refetch).toHaveBeenCalledTimes(1);
  });

  it("does not treat a partial detail as a complete saved configuration snapshot", () => {
    render(
      <ExperimentSourceGate detail={{ data: { id: c25.id, prompt_configs: [prompt] } }} configs={unavailable()}>
        <input aria-label="Edit saved experiment" />
      </ExperimentSourceGate>,
    );
    expect(screen.queryByLabelText("Edit saved experiment")).not.toBeInTheDocument();
  });

  it("reconstructs c25 saved prompt, version, EPC and model settings without runs", () => {
    const values = editValues(c25);
    expect(values.promptConfig).toHaveLength(1);
    expectSavedPrompt(values.promptConfig[0]);
    expect(entriesOf(values)[0]).toMatchObject({
      id: prompt.id,
      prompt_id: prompt.prompt_id,
      prompt_version: prompt.prompt_version,
      model: prompt.model,
      model_params: prompt.model_params,
      configuration: prompt.configuration,
      output_format: prompt.output_format,
    });
  });

  it("reconstructs c26 canonical prompt runs as editable saved cards, not external placeholders", () => {
    const values = editValues(c26);
    expect(values.promptConfig).toHaveLength(1);
    expectSavedPrompt(values.promptConfig[0]);
    expect(values.promptConfig[0].model[0].runId).toBe(canonical.run_id);
    expect(entriesOf(values)[0].model_params).toEqual(prompt.model_params);
  });

  it.each([[], null])("keeps historical saved configurations with runs=%j", (runs) => {
    const values = editValues({ ...c25, runs });
    expect(values.promptConfig).toHaveLength(1);
    expectSavedPrompt(values.promptConfig[0]);
  });

  it("retains every same-model variant and its settings through the unchanged real edit serializer", () => {
    const detail = { ...c25, prompt_configs: [prompt, secondPrompt], agent_configs: [agent] };
    const before = JSON.stringify(detail);
    const values = editValues(detail);
    expect(values.promptConfig).toHaveLength(3);
    expectSavedPrompt(values.promptConfig[0]);
    expectSavedPrompt(values.promptConfig[1], secondPrompt);
    const body = experimentBodyOfValues(values, { isEditing: true });
    const wire = serverExperimentBody(body, { isEditing: true, initial: detail });
    expect(wire.blocks).toEqual([]);
    expect(wire.body.prompt_config).toHaveLength(3);
    for (const saved of [prompt, secondPrompt]) {
      expect(wire.body.prompt_config.find((entry) => entry.id === saved.id)).toMatchObject({
        prompt_id: saved.prompt_id,
        prompt_version: saved.prompt_version,
        model: saved.model,
        model_params: saved.model_params,
        configuration: saved.configuration,
        output_format: saved.output_format,
      });
    }
    expect(wire.body.prompt_config[2]).toMatchObject({
      id: agent.id,
      agent_id: agent.agent_id,
      agent_version: agent.agent_version,
    });
    expect(JSON.stringify(detail)).toBe(before);
  });

  // W5-B-001-R1: these values were restored into cards but stripped on save.
  it.each(["c25", "c26"])(
    "preserves %s stop arrays and logit bias through an unchanged real save",
    (line) => {
      const detail = {
        ...(line === "c25" ? c25 : c26),
        prompt_configs: [prompt, secondPrompt],
      };
      const before = JSON.stringify(detail);
      const values = editValues(detail);
      const body = experimentBodyOfValues(values, { isEditing: true });
      const wire = serverExperimentBody(body, { isEditing: true, initial: detail });
      expect(values.promptConfig).toHaveLength(2);
      expect(body.prompt_config).toHaveLength(2);
      expect(wire.blocks).toEqual([]);
      expect(wire.body.prompt_config).toHaveLength(2);
      detail.prompt_configs.forEach((saved, index) => {
        const params = values.promptConfig[index].modelParams[escapeModelKey(saved.model)];
        expect(params.stop).toEqual(saved.model_params.stop);
        expect(params.logit_bias).toEqual(saved.model_params.logit_bias);
        const serialized = body.prompt_config.find((entry) => entry.id === saved.id);
        const submitted = wire.body.prompt_config.find((entry) => entry.id === saved.id);
        expect(serialized.model_params).toEqual(saved.model_params);
        expect(submitted.model_params).toEqual(saved.model_params);
        expect(submitted).toMatchObject({
          prompt_id: saved.prompt_id,
          prompt_version: saved.prompt_version,
          model: saved.model,
          configuration: saved.configuration,
          output_format: saved.output_format,
        });
      });
      expect(JSON.stringify(detail)).toBe(before);
    },
  );

  it("keeps provider JSON without allowing embedded form snapshots", () => {
    const params = { temperature: 0, stop: ["END"], logit_bias: { "123": -100 } };
    const form = { promptConfig: [{ modelParams: {} }], userEvalMetrics: [] };
    form.promptConfig[0].modelParams.saved = { ...form };
    expect(slimModelParams({ ...params, ...form, config: form })).toEqual(params);
  });

  it("keeps explicit empty provider containers", () => {
    expect(slimModelParams({ stop: [], logit_bias: {} })).toEqual({
      stop: [],
      logit_bias: {},
    });
  });

  it.each([
    { stop: [{ promptConfig: [] }], logit_bias: { "123": { modelParams: {} } } },
    { stop: ["END", 123], logit_bias: { "123": "-100" } },
    { stop: { promptConfig: [] }, logit_bias: [-100] },
    { logit_bias: { "123": NaN } },
    { logit_bias: { "123": Infinity } },
  ])("rejects non-parameter provider containers (%#)", (params) => {
    expect(slimModelParams({ temperature: 0, ...params })).toEqual({ temperature: 0 });
  });

  it("does not lose saved variants outside the inline canonical run page", () => {
    const values = editValues({
      ...c26,
      prompt_configs: [prompt, secondPrompt],
      runs_total: 2,
      runs_has_more: true,
    });
    expect(entriesOf(values).map((entry) => entry.id)).toEqual([prompt.id, secondPrompt.id]);
    expectSavedPrompt(values.promptConfig[1], secondPrompt);
  });

  it("restores agent-only saved configurations and their edit IDs", () => {
    const values = editValues({ ...c25, prompt_configs: [], agent_configs: [agent] });
    expect(values.promptConfig[0]).toMatchObject({
      targetType: "agent",
      promptConfigId: agent.id,
      agentId: agent.agent_id,
      agentVersion: agent.agent_version,
    });
    expect(entriesOf(values)[0]).toMatchObject({ id: agent.id, agent_id: agent.agent_id });
  });

  it("duplicates saved configurations without carrying EPC, agent or execution IDs", () => {
    const values = cardsFromExperiment({ ...c26, agent_configs: [agent] });
    expect(values.name).toBe("Saved experiment (copy)");
    expect(values.promptConfig).toHaveLength(2);
    expect(values.promptConfig[0].model[0].id).not.toBe(prompt.id);
    expect(values.promptConfig[0].model[0].promptConfigId).toBeUndefined();
    expect(values.promptConfig[0].model[0].runId).toBeUndefined();
    expect(values.promptConfig[1].promptConfigId).toBeUndefined();
    expect(entriesOf(values, false).every((entry) => !entry.id && !entry.run_id)).toBe(true);
    expect(entriesOf(values, false)[0].model_params).toEqual(prompt.model_params);
  });

  it("keeps prototype gating, run identity and supplementary tool setup unchanged", () => {
    const run = {
      run_id: canonical.run_id,
      target_type: "prompt",
      target: {
        prompt_id: prompt.prompt_id,
        prompt_version_id: prompt.prompt_version,
        model: prompt.model,
        model_params: { max_tokens: 64 },
      },
    };
    const detail = { ...c25, runs: [run] };
    render(
      <ExperimentSourceGate detail={{ data: detail }} configs={unavailable()}>
        <input aria-label="Edit saved experiment" />
      </ExperimentSourceGate>,
    );
    expect(screen.queryByLabelText("Edit saved experiment")).not.toBeInTheDocument();
    const values = cardsFromExperiment(detail, {
      [run.run_id]: { config: { tools: [{ name: "lookup", mode: "mock" }] } },
    }, { mode: "edit" });
    expect(values.promptConfig).toHaveLength(1);
    expect(values.promptConfig[0]).toMatchObject({
      targetType: "prompt",
      toolSetup: { mode: "mock", perTool: {} },
      model: [{ runId: run.run_id }],
    });
    expect(entriesOf(values)[0].model_params.max_tokens).toBe(64);
  });
});
