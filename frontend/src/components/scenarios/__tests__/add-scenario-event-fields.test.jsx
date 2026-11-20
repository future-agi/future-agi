import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AddScenarioModal from "../AddScenarioModal";
import { Events } from "src/utils/Mixpanel";

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    get: vi.fn(),
    post: vi.fn(),
    enqueueSnackbar: vi.fn(),
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    develop: { getDatasets: () => "/datasets/" },
    scenarios: { create: "/scenarios/" },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));
vi.mock("../ScenarioGraphBuilder", () => ({ default: () => null }));

const scenarioName = "SYNTHETIC_SCENARIO_9f3a";
const scenarioDescription = "SYNTHETIC_DESCRIPTION_4b2c";

describe("scenario creation event fields", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({
      data: {
        result: {
          datasets: [
            {
              id: "dataset-17",
              name: "Example dataset",
              number_of_datapoints: 5,
            },
          ],
        },
      },
    });
    mocks.post.mockResolvedValue({ data: { result: {} } });
  });

  it.each([
    ["with a description", scenarioDescription, true],
    ["without a description", "", false],
  ])(
    "sends dataset, kind and description presence %s",
    async (_label, description, hasDescription) => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      const onCreateSuccess = vi.fn();
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      render(
        <QueryClientProvider client={client}>
          <AddScenarioModal
            open
            onClose={onClose}
            onCreateSuccess={onCreateSuccess}
          />
        </QueryClientProvider>,
      );
      await user.type(
        screen.getByRole("textbox", { name: "Scenario Name" }),
        scenarioName,
      );
      if (description) {
        await user.type(
          screen.getByRole("textbox", { name: "Description" }),
          description,
        );
      }
      await user.click(await screen.findByText("Example dataset"));
      await user.click(screen.getByRole("button", { name: "Create Scenario" }));

      await waitFor(() => expect(onCreateSuccess).toHaveBeenCalledTimes(1));
      expect(mocks.post).toHaveBeenCalledWith("/scenarios/", {
        name: scenarioName,
        description,
        dataset_id: "dataset-17",
        kind: "dataset",
      });
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(mocks.mixpanel.track.mock.calls).toHaveLength(1);
      expect(mocks.mixpanel.track.mock.calls[0][0]).toBe(
        Events.scenarioCreateClicked,
      );
      expect(mocks.mixpanel.track.mock.calls[0][1]).toEqual({
        form_fields: {
          dataset_id: "dataset-17",
          kind: "dataset",
          has_description: hasDescription,
        },
      });
      const allEvents = JSON.stringify(mocks.mixpanel.track.mock.calls);
      expect(allEvents).not.toContain(scenarioName);
      expect(allEvents).not.toContain(scenarioDescription);
    },
  );
});
