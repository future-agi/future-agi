// "Traces I already have" where this build has no route to match traces to
// rows: the card stays, and one sentence stands in place of the match line.
// No match request is sent, and no match count is written for the Review
// step or the step checks to read. What Submit does with this card is
// pinned too: the wizard refuses the run on its Configuration step before
// any request (the create body cannot carry a traces run).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { ExperimentFormContext } from "../ExperimentFormContext";
import {
  DEFAULT_TRACES,
  emptyExperimentValues,
  newRunCard,
} from "../experimentFormValues";
import { experimentBodyOfValues } from "../experimentBody";
import { withServerBlocks, wireExperimentBody } from "../serverBody";
import { stepIssues } from "../stepIssues";
import { EXPERIMENT_STEP, PROMPT_CONFIG_TYPE } from "../constants";
import LinkedTracesCard from "../LinkedTracesCard";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: api,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetDatasetColumns: () => ({
    data: [{ id: "c1", name: "instruction", data_type: "text" }],
  }),
}));

const SENTENCE = "Matching traces to rows is not part of this release.";
const PROJECTS = [{ id: "p1", name: "Support bot" }];
const SUBMIT_REFUSAL =
  "Linked traces runs aren't supported here. Remove that run, or use a prompt or an agent.";
const tracesCard = () => ({
  ...newRunCard(PROMPT_CONFIG_TYPE.TRACES),
  traces: { ...DEFAULT_TRACES, project_id: "p1" },
});

const state = { form: null };
function Harness() {
  const form = useForm({ defaultValues: { promptConfig: [tracesCard()] } });
  state.form = form;
  return (
    <ExperimentFormContext.Provider value={{ datasetId: "ds-1" }}>
      <LinkedTracesCard
        control={form.control}
        setValue={form.setValue}
        fieldPrefix="promptConfig.0"
        letters={["A"]}
      />
    </ExperimentFormContext.Provider>
  );
}

function open() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

const matchRequests = () =>
  api.post.mock.calls.filter(
    ([url]) => String(url) === endpoints.develop.r2.imports,
  );
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  setLaunchOverrideForTest({ launch: true });
  api.get.mockImplementation((url) =>
    Promise.resolve(
      url === endpoints.project.listProjects()
        ? { data: { result: { projects: PROJECTS } } }
        : { data: { result: {} } },
    ),
  );
  api.post.mockResolvedValue({ data: { result: { summary: { matched: 0 } } } });
});

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("Traces I already have where this build cannot match traces", () => {
  it("keeps the card and says so in place of the match line", async () => {
    open();
    expect(screen.getByText("Traces I already have")).toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(screen.getByTestId("linked-match-line")).toHaveTextContent(SENTENCE);
    expect(screen.queryByText("Matching…")).not.toBeInTheDocument();
  });

  it("sends no match request and writes no match count", async () => {
    open();
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    // The card debounces its match request by 250 ms.
    await pause(400);
    expect(matchRequests()).toEqual([]);
    const card = state.form.getValues("promptConfig.0");
    expect(card._matchedRows).toBeUndefined();
    expect(card.traces.matched_count).toBeUndefined();
    expect(screen.queryByText(/rows matched/)).not.toBeInTheDocument();
  });
});

describe("Submit with a traces card is refused before any request", () => {
  it("the wizard blocks the run on its Configuration step", () => {
    const values = {
      ...emptyExperimentValues(),
      name: "Traces check",
      promptConfig: [tracesCard()],
    };
    const wire = wireExperimentBody(
      experimentBodyOfValues(values, { datasetId: "ds-1" }),
      {},
      false,
    );
    expect(wire.blocks.map((b) => b.text)).toContain(SUBMIT_REFUSAL);
    const issues = withServerBlocks(
      stepIssues(values, { columns: [] }),
      wire.blocks,
    );
    expect(issues[EXPERIMENT_STEP.CONFIGURATION]).toContain(SUBMIT_REFUSAL);
  });
});
