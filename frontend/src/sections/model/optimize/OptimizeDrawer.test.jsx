/* eslint-disable react/prop-types */
// Models > Optimize: the legacy Prompt Template optimizer is retired, and the
// create call answers 400 with one sentence in `result` (the axios
// interceptor spreads the response body into the rejection). The drawer
// shows that sentence, stays open, and still offers Prompt Template beside
// AI Output.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import OptimizeDrawer from "./OptimizeDrawer";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  enqueueSnackbar: vi.fn(),
  modelId: "6b0c2f9e-4d51-4a8e-9f4e-2b7d1c3a5e60",
  metric: {
    id: "3f9a7c1e-2b64-4d0a-8e15-7c2d9b4a6f10",
    name: "Answer accuracy",
  },
}));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    optimization: { createOptimization: "/model-hub/optimize-dataset/" },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("./NoMetricAdded", () => ({ default: () => null }));
vi.mock("src/components/FormDateField", () => ({ FormDateField: () => null }));
vi.mock("src/api/model/metric", () => ({
  useGetMetricOptions: () => ({ data: [] }),
  useGetAllCustomMetrics: () => ({ data: [mocks.metric] }),
}));
vi.mock("src/routes/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ id: mocks.modelId }),
}));
vi.mock("src/components/FormTextField/FormTextFieldV2", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    default: ({ control, fieldName, label }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <label>
            {label}
            <input {...field} value={field.value ?? ""} />
          </label>
        )}
      />
    ),
  };
});
// FormSelectField as a native select bound to the same form field, so the
// offered options are read without MUI's menu portal.
vi.mock("src/components/FormSelectField", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    FormSelectField: ({ control, fieldName, label, options }) => (
      <Controller
        control={control}
        name={fieldName}
        render={({ field }) => (
          <label>
            {label}
            <select {...field} value={field.value ?? ""}>
              <option value="">Select</option>
              {options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        )}
      />
    ),
  };
});

const RETIRED =
  "This legacy optimizer (optimize-dataset PromptTemplate) is no longer available. Use Dataset Optimization instead.";

// Environment, version and dates come from the dataset the drawer was
// opened from, so only name, type and one metric are picked here.
const initialData = {
  environment: 1,
  version: "v1",
  startDate: new Date("2026-01-01T00:00:00Z"),
  endDate: new Date("2026-01-02T00:00:00Z"),
};

const renderDrawer = () => {
  const onClose = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <OptimizeDrawer open onClose={onClose} initialData={initialData} />
    </QueryClientProvider>,
  );
  return { onClose };
};

const optimizeSelect = () =>
  screen.getByRole("combobox", { name: "Optimize", hidden: true });

describe("OptimizeDrawer: retired Prompt Template optimizer", () => {
  beforeEach(() => vi.clearAllMocks());

  it("offers Prompt Template beside AI Output", () => {
    renderDrawer();

    const select = optimizeSelect();
    expect(
      within(select).getByRole("option", {
        name: "Prompt Template",
        hidden: true,
      }).value,
    ).toBe("PromptTemplate");
    expect(
      within(select).getByRole("option", { name: "AI Output", hidden: true })
        .value,
    ).toBe("RightAnswer");
  });

  it("shows the server's sentence when the create call answers 400, and keeps the option", async () => {
    mocks.post.mockRejectedValue({
      status: false,
      result: RETIRED,
      message: RETIRED,
      statusCode: 400,
    });
    const { onClose } = renderDrawer();

    fireEvent.change(
      screen.getByRole("textbox", { name: "Name", hidden: true }),
      { target: { value: "Legacy template run" } },
    );
    fireEvent.change(optimizeSelect(), {
      target: { value: "PromptTemplate" },
    });

    const metricPicker = screen
      .getAllByRole("combobox", { hidden: true })
      .find((element) => element.tagName !== "SELECT");
    fireEvent.mouseDown(metricPicker);
    fireEvent.click(
      await screen.findByRole("option", { name: mocks.metric.name }),
    );
    const addMetric = document.getElementById("optimize-no-metric-add");
    await waitFor(() => expect(addMetric).toBeEnabled());
    fireEvent.click(addMetric);

    fireEvent.click(
      screen.getByRole("button", { name: "Optimize dataset", hidden: true }),
    );

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        expect.objectContaining({ message: RETIRED, variant: "error" }),
      ),
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith(
      `/model-hub/optimize-dataset/${mocks.modelId}/`,
      expect.objectContaining({
        optimize_type: "PromptTemplate",
        model: mocks.modelId,
        metrics: [mocks.metric.id],
      }),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      expect.objectContaining({ variant: "success" }),
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(
      within(optimizeSelect()).getByRole("option", {
        name: "Prompt Template",
        hidden: true,
      }).value,
    ).toBe("PromptTemplate");
  });
});
