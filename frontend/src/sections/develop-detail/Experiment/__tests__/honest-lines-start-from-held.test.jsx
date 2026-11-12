// Honest lines row 22, after W8: Start from reads the run list
// (/model-hub/datasets/{id}/runs/) the way the Experiments tab does. In a
// build that installs the absent-path guard (a launch build without
// SHOW_UNRELEASED_PARTS), the guard refuses that read for every reader, so
// Start from must say "Not available in this release" and send nothing,
// instead of an error that offers a retry which can never succeed.
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import StartFromExperiment from "../StartFromExperiment";

const NOT_IN_RELEASE = "Not available in this release";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  datasetId: "5e7a0000-0000-4000-8000-000000000022",
}));
const RUNS_URL = `/model-hub/datasets/${h.datasetId}/runs/`;

// A build that installs the absent-path guard.
vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  SHOW_UNRELEASED_PARTS: false,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get: h.get } };
});
vi.mock("../ExperimentFormContext", () => ({
  useExperimentForm: () => ({
    datasetId: h.datasetId,
    startFromPreset: null,
    versions: [{ id: "version-2", number: 2 }],
  }),
}));
vi.mock("src/sections/common/ux/oneview/layoutPrefs", () => ({
  useLayoutPrefs: () => [{}, () => {}],
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RunLetterBadge", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));

function Host() {
  const form = useForm({ defaultValues: { startFrom: null, promptConfig: [] } });
  return (
    <StartFromExperiment
      control={form.control}
      setValue={form.setValue}
      getValues={form.getValues}
    />
  );
}

const mount = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Host />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  h.get.mockReset().mockRejectedValue({
    statusCode: 404,
    message: "Native run provenance is unavailable",
  });
});
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Start from when the build refuses the run list", () => {
  it("says Not available in this release, sends nothing, and offers no retry", async () => {
    setLaunchOverrideForTest({ launch: true });
    mount();
    expect(await screen.findByText(NOT_IN_RELEASE)).toBeTruthy();
    expect(screen.queryByText(/Could not load previous experiments/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Retry experiments" }),
    ).toBeNull();
    expect(screen.queryByText(/Loading experiments/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Start from experiment" }),
    ).toHaveProperty("disabled", true);
    expect(h.get).not.toHaveBeenCalled();
  });

  it("keeps the error and its retry for a real read failure", async () => {
    mount();
    expect(
      await screen.findByText(/Could not load previous experiments/),
    ).toBeTruthy();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry experiments" }));
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get.mock.calls.map(([url]) => url)).toEqual([RUNS_URL, RUNS_URL]);
  });
});
