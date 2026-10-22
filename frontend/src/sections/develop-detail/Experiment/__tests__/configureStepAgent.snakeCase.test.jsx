// Regression test for the experiment snake_case hotfix (D14, ours-experiments.md
// §11 #18): Run Experiment > Configure, adding an agent.
// The agent list is GraphListSerializer output
// (agent_playground/serializers/graph.py:29-62); its latest version id is
// `active_version_id` (:38-40). On main the picker read `activeVersionId`,
// fell back to the graph id, and AgentPromptRenderer kept that pre-set value
// (pickupLatestVersion starts false, AgentPromptRenderer.jsx:67, 196-205), so
// create answered 400 "Agent version ... not found" (model_hub/views/experiments.py:4029-4033).
import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useForm, useWatch } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AGENT_GRAPH_ID,
  AGENT_GRAPH_VERSION_ID,
  graphListResponse,
  graphVersionsResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, get: (...args) => axiosGetMock(...args) },
  };
});

import ConfigureStepLLM from "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepLLM";

const AgentVersionProbe = ({ control }) => {
  const agentVersion = useWatch({
    control,
    name: "promptConfig.0.agentVersion",
  });
  return <output aria-label="agent version">{agentVersion ?? ""}</output>;
};
AgentVersionProbe.propTypes = { control: PropTypes.object.isRequired };

const ConfigureStepHarness = () => {
  const {
    control,
    setValue,
    getValues,
    watch,
    unregister,
    setError,
    clearErrors,
    formState: { errors },
  } = useForm({ defaultValues: { promptConfig: [] } });
  return (
    <>
      <ConfigureStepLLM
        control={control}
        setValue={setValue}
        errors={errors}
        allColumns={[]}
        getValues={getValues}
        watch={watch}
        unregister={unregister}
        setError={setError}
        clearErrors={clearErrors}
      />
      <AgentVersionProbe control={control} />
    </>
  );
};

describe("Run Experiment Configure step reads the agent list's snake_case keys", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockImplementation((url) => {
      const path = String(url);
      if (path.endsWith(`/agent-playground/graphs/${AGENT_GRAPH_ID}/versions/`))
        return Promise.resolve({ data: graphVersionsResponse });
      if (path.endsWith("/agent-playground/graphs/"))
        return Promise.resolve({ data: graphListResponse });
      return Promise.reject(new Error(`unexpected GET ${path}`));
    });
  });

  it("D14: an added agent carries its `active_version_id`, not the graph id, as agentVersion", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ConfigureStepHarness />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByText("Add Prompt/Agents"));
    fireEvent.click(screen.getByText("Agent"));
    fireEvent.click(await screen.findByText("support-triage-agent"));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    // The renderer now fetches this graph's versions for the version select.
    await waitFor(() =>
      expect(axiosGetMock).toHaveBeenCalledWith(
        expect.stringMatching(
          new RegExp(`/agent-playground/graphs/${AGENT_GRAPH_ID}/versions/$`),
        ),
        expect.anything(),
      ),
    );
    // The version select resolves the pre-set id to its label once the
    // versions arrive; a graph id never matches and leaves the placeholder.
    expect(await screen.findByText("v3")).toBeInTheDocument();
    expect(screen.queryByText("Select version")).not.toBeInTheDocument();
    expect(screen.getByLabelText("agent version")).toHaveTextContent(
      AGENT_GRAPH_VERSION_ID,
    );
    expect(screen.getByLabelText("agent version")).not.toHaveTextContent(
      AGENT_GRAPH_ID,
    );
  });
});
