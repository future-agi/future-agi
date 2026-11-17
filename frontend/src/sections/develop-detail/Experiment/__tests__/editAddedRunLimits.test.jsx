// Edit › Configuration: a prompt or agent run added to an experiment that
// holds a time limit starts with that limit, so every run keeps the one
// experiment-wide policy and Save is not held on limits the person never
// set. A new experiment's added runs start without limits, as before.
import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { availabilityOfReport } from "src/sections/common/RunSources/runSourceAvailability";
import ConfigureStepLLM from "../StepperComponentExperiment/ConfigureStepLLM";
import { emptyExperimentValues } from "../experimentFormValues";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";
import { withStoredRunSettings } from "../editRunSettings";

const harness = vi.hoisted(() => ({
  availability: null,
  picker: null,
  target: null,
  form: null,
}));

vi.mock("src/sections/common/RunSources/useRunSourceAvailability", () => ({
  useRunSourceAvailability: () => harness.availability,
}));
// The picker hands a kind back through onAddCard.
vi.mock("src/sections/common/RunSources/SourcePicker", () => ({
  default: (props) => {
    harness.picker = props;
    return <div data-testid="source-picker" />;
  },
}));
// The prompt / agent list hands the chosen item back through onPick.
vi.mock("../RunTargetPicker", () => ({
  default: (props) => {
    harness.target = props;
    return null;
  },
}));
vi.mock("../HttpRunForm", () => ({ default: () => null }));
vi.mock("../EndpointLimitsRow", () => ({ default: () => null }));
vi.mock("../StepperComponentExperiment/AgentPromptRenderer", () => ({
  default: () => <div data-testid="agent-prompt-renderer" />,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows", dataset: { kind: "rows" } }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useRunConfigs: () => ({ data: null }),
}));
vi.mock("../reservedIds", async (importOriginal) => ({
  ...(await importOriginal()),
  useReservedIds: () => ({ data: null }),
}));
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => ({ data: { pages: [] }, isPending: false }),
}));

const YES = { available: true, reason: null };
const ready = () =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: { prompt: YES, agent: YES, http: YES },
  });

const DATASET = "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01";
const PROMPT = "1b2c3d4e-0000-4000-8000-000000000001";
const ADDED_PROMPT = "1b2c3d4e-0000-4000-8000-000000000005";
const ADDED_VERSION = "1b2c3d4e-0000-4000-8000-000000000006";
const GRAPH = "6a6a6a6a-0000-4000-8000-000000000001";
const GRAPH_VERSION = "6a6a6a6a-0000-4000-8000-000000000002";

const savedPrompt = (runId, promptVersion) => ({
  _itemId: runId,
  experimentType: "llm",
  targetType: "prompt",
  promptId: PROMPT,
  promptVersion,
  model: [{ value: "gpt-4o-mini", providers: "openai" }],
  modelParams: {},
  configuration: { toolChoice: "auto", tools: [] },
  name: "Saved prompt",
  outputFormat: "string",
  variables: [],
  variableMapping: {},
  runId,
});

// The Edit form as it opens on an experiment that holds a 30 s time limit.
const editValues = () =>
  withStoredRunSettings(
    {
      ...emptyExperimentValues(),
      name: "Exp 1",
      promptConfig: [
        savedPrompt("run-a", "1b2c3d4e-0000-4000-8000-000000000002"),
        savedPrompt("run-b", "1b2c3d4e-0000-4000-8000-000000000003"),
      ],
    },
    {
      id: "e0e0e0e0-0000-4000-8000-000000000001",
      name: "Exp 1",
      description: "",
      metadata: {},
      error_policy: null,
      limits: { timeout_s: 30 },
    },
  );

function Configure({ values, isEditing = false }) {
  const form = useForm({ defaultValues: values });
  harness.form = form;
  return <ConfigureStepLLM {...form} isEditing={isEditing} />;
}
Configure.propTypes = {
  values: PropTypes.object.isRequired,
  isEditing: PropTypes.bool,
};

// `Add run`, then the kind, then the item the list hands back.
const addRun = (kind, item) => {
  fireEvent.click(
    within(screen.getByTestId("runs-heading")).getByRole("button", {
      name: "Add run",
    }),
  );
  act(() => harness.picker.onAddCard(kind, document.createElement("div")));
  act(() => harness.target.onPick(item));
};

const ADD = {
  prompt: () => {
    addRun("prompt", {
      id: ADDED_PROMPT,
      name: "Added prompt",
      model: "gpt-4o-mini",
    });
    // The person picks its version on the card.
    act(() =>
      harness.form.setValue("promptConfig.2.promptVersion", ADDED_VERSION),
    );
  },
  agent: () =>
    addRun("agent", {
      id: GRAPH,
      name: "Support agent",
      active_version_id: GRAPH_VERSION,
    }),
};

const editWire = (values) =>
  serverExperimentBody(
    experimentBodyOfValues(values, { isEditing: true, datasetId: DATASET }),
    { isEditing: true, initial: { name: "Exp 1", description: "" } },
  );

beforeEach(() => {
  // The enhanced Configuration step (exp.new) in a non-launch test build.
  setLaunchOverrideForTest({ launch: false });
  harness.availability = ready();
  harness.picker = null;
  harness.target = null;
  harness.form = null;
  useRunExperimentStore.setState({ firstRunType: null, prefill: null });
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  useRunExperimentStore.setState({ firstRunType: null, prefill: null });
});

const KINDS = [
  ["a prompt", "prompt"],
  ["an agent", "agent"],
];

describe("a run added on Edit to an experiment that holds a time limit", () => {
  it.each(KINDS)(
    "%s run starts with the stored limit, and Save sends no limits",
    (_label, kind) => {
      render(<Configure values={editValues()} isEditing />);
      ADD[kind]();
      const cards = harness.form.getValues("promptConfig");
      expect(cards).toHaveLength(3);
      expect(cards[2]).toMatchObject({ targetType: kind });
      expect(cards[2].runId).toBeUndefined();
      expect(cards[2].rowLimits).toEqual({ timeout_s: 30 });
      const wire = editWire(harness.form.getValues());
      expect(wire.body.prompt_config).toHaveLength(3);
      expect(wire.body).not.toHaveProperty("limits");
      wire.body.prompt_config.forEach((entry) =>
        expect(entry).not.toHaveProperty("row_limits"),
      );
      const fields = wire.blocks.map((block) => block.field);
      expect(fields).not.toContain("limits");
      expect(fields).not.toContain("prompt_config[].row_limits");
    },
  );
});

describe("a run added to a new experiment", () => {
  it.each(KINDS)(
    "%s run starts without limits, whatever the other runs carry",
    (_label, kind) => {
      render(
        <Configure
          values={{
            ...emptyExperimentValues(),
            promptConfig: [
              {
                ...savedPrompt(
                  undefined,
                  "1b2c3d4e-0000-4000-8000-000000000002",
                ),
                rowLimits: { timeout_s: 30 },
              },
              savedPrompt(undefined, "1b2c3d4e-0000-4000-8000-000000000003"),
            ],
          }}
        />,
      );
      ADD[kind]();
      const cards = harness.form.getValues("promptConfig");
      expect(cards).toHaveLength(3);
      expect(cards[2]).toMatchObject({ targetType: kind });
      expect(cards[2].rowLimits).toBeUndefined();
    },
  );
});
