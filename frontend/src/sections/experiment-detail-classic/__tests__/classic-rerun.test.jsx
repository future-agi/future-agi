import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";

const state = vi.hoisted(() => ({
  shown: true,
  supported: true,
  append: vi.fn(),
  get: vi.fn(),
}));
vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: () => state.shown,
}));
vi.mock("src/api/develop/experiment-append", async (original) => ({
  ...(await original()),
  appendVariantSupported: () => state.supported,
  appendExperimentVariant: (...args) => state.append(...args),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
      },
    },
  },
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => ({
    data: {
      pages: [{ results: [{ id: "prompt-a", name: "Original prompt" }] }],
    },
  }),
  usePromptVersions: () => ({
    data: {
      pages: [
        {
          results: [
            { id: "version-a", template_version: "v1", is_draft: false },
            { id: "version-b", template_version: "v2", is_draft: false },
            { id: "draft", template_version: "Draft", is_draft: true },
          ],
        },
      ],
    },
  }),
}));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: ({ value, onChange, disabledClick }) => (
    <select
      aria-label="Model"
      value={value}
      disabled={disabledClick}
      onChange={(e) =>
        onChange({ target: { value: { model_name: e.target.value } } })
      }
    >
      <option value="model-a">Model A</option>
      <option value="model-b">Model B</option>
    </select>
  ),
}));

import ClassicRerunAction from "../ClassicRerunAction";
import { appendVariantBody } from "src/api/develop/experiment-append";

const source = {
  id: "original-config",
  name: "Original variant",
  model: "model-a",
  model_config: { seed: 3 },
  model_display_name: "Model A",
  prompt_id: "prompt-a",
  prompt_name: "Original prompt",
  prompt_version: "version-a",
  prompt_version_name: "v1",
  model_params: { temperature: 0, max_tokens: 64 },
  configuration: { toolChoice: "auto", tools: [] },
  output_format: "string",
};
// XP bridge d10ac7b614ef (composed by e9d9d8b795f) requires a success
// envelope and a complete run catalog, including for classic-only configs.
const detailRead = (status) => ({
  data: {
    status: true,
    result: {
      status,
      experiment_type: "llm",
      prompt_configs: [source],
      runs: [],
      runs_total: 0,
      runs_has_more: false,
      runs_next: null,
    },
  },
});
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ClassicRerunAction experimentId="experiment-a" />
    </QueryClientProvider>,
  );
}
const flush = (ms = 0) => act(async () => vi.advanceTimersByTimeAsync(ms));
async function runModelVariant() {
  fireEvent.click(
    screen.getByText("Re-run with changes", { selector: "button" }),
  );
  fireEvent.change(screen.getByLabelText("Model"), {
    target: { value: "model-b" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
  await flush(10);
}
beforeEach(() => {
  state.shown = true;
  state.supported = true;
  state.append.mockReset().mockResolvedValue({});
  state.get.mockReset().mockResolvedValue(detailRead("Completed"));
});
afterEach(() => {
  cleanup();
  focusManager.setFocused(undefined);
  vi.useRealTimers();
});

describe("classic re-run with changes", () => {
  it("does not read or render while the claim or contract is absent", () => {
    state.shown = false;
    const view = mount();
    expect(
      screen.queryByRole("button", { name: "Re-run with changes" }),
    ).toBeNull();
    expect(state.get).not.toHaveBeenCalled();
    view.unmount();
    state.shown = true;
    state.supported = false;
    mount();
    expect(state.get).not.toHaveBeenCalled();
  });

  it("appends only a new model variant and never sends the original id", async () => {
    const before = JSON.stringify(source);
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Re-run with changes" }),
    );
    expect(
      screen.getByRole("button", { name: "Run new variant" }).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "model-b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledOnce());
    expect(state.append).toHaveBeenCalledWith("experiment-a", {
      append_only: true,
      prompt_config: [
        {
          name: "Original variant variant",
          prompt_id: "prompt-a",
          prompt_version: "version-a",
          model: "model-b",
          model_params: {},
          configuration: source.configuration,
          output_format: "string",
        },
      ],
    });
    expect(JSON.stringify(source)).toBe(before);
  });

  it("keeps the full model parameters for a prompt-version change", () => {
    const body = appendVariantBody(source, {
      name: "Version two",
      promptId: "prompt-a",
      versionId: "version-b",
      model: "model-a",
    });
    expect(body.prompt_config[0]).toEqual({
      name: "Version two",
      prompt_id: "prompt-a",
      prompt_version: "version-b",
      model: { name: "model-a", display_name: "Model A", config: { seed: 3 } },
      model_params: source.model_params,
      configuration: source.configuration,
      output_format: "string",
    });
    expect(body.prompt_config[0]).not.toHaveProperty("id");
  });

  it("retries the identical body after an ambiguous admission", async () => {
    state.append.mockRejectedValueOnce({
      response: { status: 503, data: { detail: "Admission unconfirmed" } },
    });
    mount();
    fireEvent.click(
      await screen.findByRole("button", { name: "Re-run with changes" }),
    );
    fireEvent.change(screen.getByLabelText("Model"), {
      target: { value: "model-b" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
    await screen.findByRole("button", { name: "Retry submission" });
    expect(screen.getByLabelText("Model").disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Retry submission" }));
    await waitFor(() => expect(state.append).toHaveBeenCalledTimes(2));
    expect(state.append.mock.calls[0]).toEqual(state.append.mock.calls[1]);
  });
});

describe("classic re-run status while an execution is pending", () => {
  beforeEach(() => vi.useFakeTimers());

  it("becomes available when a running experiment completes, then stops reading", async () => {
    state.get
      .mockResolvedValueOnce(detailRead("Running"))
      .mockResolvedValueOnce(detailRead("Running"))
      .mockResolvedValue(detailRead("Completed"));
    mount();
    await flush();
    const action = screen.getByText("Re-run with changes", {
      selector: "button",
    });
    expect(action.disabled).toBe(true);
    await flush(60_000);
    expect(action.disabled).toBe(false);
    expect(state.get).toHaveBeenCalledTimes(3);
    await flush(600_000);
    expect(state.get).toHaveBeenCalledTimes(3);
  });

  it("backs off while queued and never reads from a hidden tab", async () => {
    state.get.mockResolvedValue(detailRead("Queued"));
    mount();
    await flush();
    await flush(600_000);
    const reads = state.get.mock.calls.length;
    // Delays of 2 s doubling to 15 s: 43 reads in ten minutes; a fixed 2 s
    // poll would make 300.
    expect(reads).toBeGreaterThan(5);
    expect(reads).toBeLessThanOrEqual(1 + Math.ceil(600_000 / 15_000) + 3);
    act(() => focusManager.setFocused(false));
    await flush(600_000);
    expect(state.get).toHaveBeenCalledTimes(reads);
  });

  it("stops after a failed status read and offers a deliberate refresh", async () => {
    state.get
      .mockResolvedValueOnce(detailRead("Running"))
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValue(detailRead("Completed"));
    mount();
    await flush();
    const action = screen.getByText("Re-run with changes", {
      selector: "button",
    });
    await flush(600_000);
    expect(state.get).toHaveBeenCalledTimes(2);
    expect(action.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await flush();
    expect(state.get).toHaveBeenCalledTimes(3);
    expect(action.disabled).toBe(false);
    expect(screen.queryByRole("button", { name: "Refresh status" })).toBeNull();
  });

  it("runs a second variant after the first completes without a remount", async () => {
    state.get
      .mockResolvedValueOnce(detailRead("Completed"))
      .mockResolvedValueOnce(detailRead("Queued"))
      .mockResolvedValueOnce(detailRead("Running"))
      .mockResolvedValue(detailRead("Completed"));
    mount();
    await flush();
    const action = screen.getByText("Re-run with changes", {
      selector: "button",
    });
    expect(action.disabled).toBe(false);
    await runModelVariant();
    expect(state.append).toHaveBeenCalledTimes(1);
    expect(action.disabled).toBe(true);
    await flush(60_000);
    expect(action.disabled).toBe(false);
    await runModelVariant();
    expect(state.append).toHaveBeenCalledTimes(2);
    expect(state.append.mock.calls[1]).toEqual(state.append.mock.calls[0]);
  });
});
