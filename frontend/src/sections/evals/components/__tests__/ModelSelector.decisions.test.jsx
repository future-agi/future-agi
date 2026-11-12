/* eslint-disable react/prop-types -- Test-only controlled harness and mocked components. */
import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import ModelSelector from "../ModelSelector";

const mocks = vi.hoisted(() => ({ jevLocked: false }));
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => mocks.jevLocked,
  useFeatureAllowed: () => ({ allowed: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: () => null,
}));

const routerJev = "decision/openrouter/typesafe/jev-1.14";
const routerLuna = "decision/openrouter/openai/gpt-6-luna-decisions";
const nativeLuna = "decision/openai/gpt-6-luna";
let saved;
let groups;

const connection = (model_name, provider, label) => ({
  model_name,
  provider,
  label,
  is_available: true,
});
const group = (
  model_name,
  label,
  author,
  connections,
  question_types = ["noul", "choice", "score"],
) => ({
  model_name,
  label,
  author,
  connections,
  question_types,
  type: "decision",
  version: "versioned",
  min_choices: 1,
  max_choices: 255,
  max_state_bytes: 120000,
  unavailable_reason: null,
  notice: "Text and JSON inputs only.",
});
const providers = () => [
  {
    provider: "typesafe",
    display_name: "TypeSafe",
    type: "text",
    has_key: true,
  },
  { provider: "openai", display_name: "OpenAI", type: "text", has_key: true },
  {
    provider: "openrouter",
    display_name: "OpenRouter",
    type: "text",
    has_key: saved,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.jevLocked = false;
  saved = false;
  groups = [
    group("typesafe/jev-1.14", "Jev 1.14", "TypeSafe", [
      connection("jev-1.14.0", "typesafe", "TypeSafe"),
      connection(routerJev, "openrouter", "OpenRouter"),
    ]),
    group("openai/gpt-6-luna-decisions", "GPT-6 Luna Decisions", "OpenAI", [
      connection(nativeLuna, "openai", "OpenAI"),
      connection(routerLuna, "openrouter", "OpenRouter"),
    ]),
    group(
      "respan/span-01",
      "Span-01",
      "Respan",
      [
        connection(
          "decision/openrouter/respan/span-01",
          "openrouter",
          "OpenRouter",
        ),
      ],
      ["noul"],
    ),
  ];
  axios.get.mockImplementation(async (url, { params } = {}) => {
    if (url === endpoints.develop.apiKey.status)
      return { data: { result: { providers: providers() } } };
    if (url === endpoints.develop.modelList)
      return {
        data: {
          count: params?.model_type === "decision" ? groups.length : 1,
          results:
            params?.model_type === "decision"
              ? groups.map((item) => ({
                  ...item,
                  connections: item.connections.map((route) => ({
                    ...route,
                    is_available: route.provider !== "openrouter" || saved,
                  })),
                }))
              : [{ model_name: "ordinary-model", providers: "custom" }],
          next: null,
          current_page: 1,
        },
      };
    return { data: { results: [], result: { table_data: [], total_rows: 0 } } };
  });
  axios.post.mockImplementation(async () => {
    saved = true;
    return { data: { status: true } };
  });
});

function Editor({
  initialModel = "ordinary-model",
  outputType = "pass_fail",
  onModelChange,
}) {
  const [model, setModel] = useState(initialModel);
  const [mode, setMode] = useState("auto");
  return (
    <>
      <output aria-label="Selected model">{model}</output>
      <ModelSelector
        model={model}
        onModelChange={(value) => {
          setModel(value);
          onModelChange?.(value);
        }}
        mode={mode}
        onModeChange={setMode}
        outputType={outputType}
      />
    </>
  );
}
const mount = (props) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <Editor {...props} />
    </QueryClientProvider>,
  );
async function openPicker() {
  await userEvent.click(
    screen.getByRole("button", { name: /^Evaluation model:/ }),
  );
  await screen.findByRole("menuitem", { name: /GPT-6 Luna Decisions/ });
}

describe("Decision-model picker", () => {
  it.each(["success", "error"])(
    "keeps a missing-key choice actionable during search and ignores late %s after closing the drawer",
    async (outcome) => {
      const implementation = axios.get.getMockImplementation();
      let resolveSearch;
      let rejectSearch;
      const pendingSearch = new Promise((resolve, reject) => {
        resolveSearch = resolve;
        rejectSearch = reject;
      });
      const response = { data: { results: [], next: null, current_page: 1 } };
      axios.get.mockImplementation(async (url, options) => {
        if (
          url === endpoints.develop.modelList &&
          options?.params?.search === "Jev"
        ) {
          return pendingSearch;
        }
        const result = await implementation(url, options);
        if (options?.params?.model_type === "decision") {
          result.data.results.forEach((item) =>
            item.connections.forEach((route) => {
              if (route.provider === "typesafe") route.is_available = false;
            }),
          );
        }
        if (url === endpoints.develop.apiKey.status) {
          result.data.result.providers.find(
            (provider) => provider.provider === "typesafe",
          ).has_key = false;
        }
        return result;
      });
      const onModelChange = vi.fn();
      const view = mount({ onModelChange });
      await openPicker();
      await waitFor(() =>
        expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
      );
      vi.useFakeTimers();
      try {
        fireEvent.change(screen.getByPlaceholderText("Search models..."), {
          target: { value: "Jev" },
        });
        expect(axios.get).not.toHaveBeenCalledWith(
          endpoints.develop.modelList,
          expect.objectContaining({
            params: expect.objectContaining({ search: "Jev" }),
          }),
        );
        await act(async () => {
          await vi.advanceTimersByTimeAsync(400);
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1);
        });
        expect(axios.get).toHaveBeenCalledWith(endpoints.develop.modelList, {
          params: { page: 1, search: "Jev", model_type: "llm" },
        });
        expect(axios.get).toHaveBeenCalledWith(endpoints.develop.modelList, {
          params: { model_type: "decision", limit: 100 },
        });
        const row = screen.getByRole("menuitem", { name: /Jev 1.14/ });
        expect(row).not.toHaveAttribute("aria-disabled", "true");
        expect(row).toHaveTextContent("Add your TypeSafe API key");
        expect(screen.getByRole("progressbar")).toBeInTheDocument();
        fireEvent.click(row);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(250);
        });
        expect(screen.getAllByText("Configure API keys")).toHaveLength(1);
        expect(
          screen.queryByPlaceholderText("Search models..."),
        ).not.toBeInTheDocument();
        fireEvent.click(
          screen.getByRole("button", { name: "Close API key configuration" }),
        );
        await act(async () => {
          if (outcome === "error")
            rejectSearch(new Error("ordinary search unavailable"));
          else resolveSearch(response);
          await vi.advanceTimersByTimeAsync(500);
        });
        expect(
          screen.queryByText("Configure API keys"),
        ).not.toBeInTheDocument();
        expect(
          screen.queryByPlaceholderText("Search models..."),
        ).not.toBeInTheDocument();
        expect(screen.getByLabelText("Selected model")).toHaveTextContent(
          "ordinary-model",
        );
        expect(onModelChange).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
      } finally {
        resolveSearch(response);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(500);
        });
        view.unmount();
        vi.clearAllTimers();
        vi.useRealTimers();
      }
    },
  );

  it("keeps configured-key keyboard selection singular while ordinary search completes", async () => {
    const implementation = axios.get.getMockImplementation();
    let resolveSearch;
    const pendingSearch = new Promise((resolve) => {
      resolveSearch = resolve;
    });
    const response = { data: { results: [], next: null, current_page: 1 } };
    axios.get.mockImplementation((url, options) =>
      url === endpoints.develop.modelList && options?.params?.search === "Jev"
        ? pendingSearch
        : implementation(url, options),
    );
    const onModelChange = vi.fn();
    const view = mount({ onModelChange });
    await openPicker();
    await waitFor(() =>
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
    );
    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByPlaceholderText("Search models..."), {
        target: { value: "Jev" },
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      const row = screen.getByRole("menuitem", { name: /Jev 1.14/ });
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
      row.focus();
      fireEvent.keyDown(row, { key: "Enter", code: "Enter" });
      await act(async () => {
        resolveSearch(response);
        await vi.advanceTimersByTimeAsync(500);
      });
      expect(onModelChange).toHaveBeenCalledTimes(1);
      expect(onModelChange).toHaveBeenCalledWith("jev-1.14.0");
      expect(screen.getByLabelText("Selected model")).toHaveTextContent(
        "jev-1.14.0",
      );
      expect(
        screen.queryByPlaceholderText("Search models..."),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("Configure API keys")).not.toBeInTheDocument();
    } finally {
      resolveSearch(response);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      view.unmount();
      vi.clearAllTimers();
      vi.useRealTimers();
    }
  });

  it("groups direct and OpenRouter connections under one model and searches connection names", async () => {
    mount();
    await openPicker();
    expect(
      screen.getAllByRole("menuitem", { name: /GPT-6 Luna Decisions/ }),
    ).toHaveLength(1);
    expect(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
    ).toHaveValue(nativeLuna);
    await userEvent.type(
      screen.getByPlaceholderText("Search models..."),
      "OpenRouter",
    );
    expect(
      screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Turing Large")).not.toBeInTheDocument();
  });

  it("offers the OpenRouter connection when the search names it", async () => {
    mount();
    await openPicker();
    await userEvent.type(
      screen.getByPlaceholderText("Search models..."),
      "openrouter",
    );
    // Only the native key is saved, yet the search asked for OpenRouter.
    expect(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
    ).toHaveValue(routerLuna);
    await userEvent.click(
      screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ }),
    );
    await screen.findByText("Configure API keys");
    expect(
      (await screen.findByText("OpenRouter", { exact: true })).closest("form"),
    ).toBeTruthy();
    // Nothing is selected until the OpenRouter key exists.
    expect(screen.getByLabelText("Selected model")).toHaveTextContent(
      "ordinary-model",
    );
  });

  it("makes the connection selector reachable from a model row with the keyboard", async () => {
    mount();
    await openPicker();
    const row = screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ });
    row.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
    ).toHaveFocus();
  });

  it("saves the explicitly chosen OpenRouter route through the real key drawer and enters Quick mode", async () => {
    mount();
    await openPicker();
    await userEvent.selectOptions(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
      routerLuna,
    );
    await screen.findByText("Configure API keys");
    const form = (
      await screen.findByText("OpenRouter", { exact: true })
    ).closest("form");
    await userEvent.type(
      within(form).getByLabelText("API Key"),
      "synthetic-openrouter-key",
    );
    await userEvent.click(within(form).getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Selected model")).toHaveTextContent(
        routerLuna,
      ),
    );
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.apiKey.create,
      expect.objectContaining({ provider: "openrouter" }),
    );
    expect(
      await screen.findByRole("button", {
        name: "Evaluation model: GPT-6 Luna Decisions · OpenRouter",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Evaluation mode: Quick" }),
    ).toBeInTheDocument();
  });

  it("keeps the original selection and mode after cancelling OpenRouter key setup", async () => {
    mount();
    await openPicker();
    await userEvent.selectOptions(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
      routerLuna,
    );
    await screen.findByText("Configure API keys");
    await userEvent.click(
      screen.getByRole("button", { name: "Close API key configuration" }),
    );
    expect(screen.getByLabelText("Selected model")).toHaveTextContent(
      "ordinary-model",
    );
    expect(
      screen.getByRole("button", { name: "Evaluation mode: Auto" }),
    ).toBeInTheDocument();
    await openPicker();
    expect(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
    ).toHaveValue(nativeLuna);
  });

  it("retains a saved OpenRouter connection without a key instead of silently switching to native", async () => {
    mount({ initialModel: routerLuna });
    await openPicker();
    expect(
      screen.getByRole("combobox", {
        name: "Connection for GPT-6 Luna Decisions",
      }),
    ).toHaveValue(routerLuna);
    expect(
      screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ }),
    ).toHaveTextContent("Add your OpenRouter API key");
    expect(screen.getByLabelText("Selected model")).toHaveTextContent(
      routerLuna,
    );
  });

  it("does not gate non-Jev BYOK models on the native Jev entitlement", async () => {
    mocks.jevLocked = true;
    mount();
    await openPicker();
    expect(screen.getByRole("menuitem", { name: /Jev 1.14/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    const jevRoutes = screen.getByRole("combobox", {
      name: "Connection for Jev 1.14",
    });
    expect(
      Array.from(jevRoutes.options).every((option) => option.disabled),
    ).toBe(true);
    const luna = screen.getByRole("menuitem", { name: /GPT-6 Luna Decisions/ });
    expect(luna).not.toHaveAttribute("aria-disabled", "true");
    await userEvent.click(luna);
    expect(screen.getByLabelText("Selected model")).toHaveTextContent(
      nativeLuna,
    );
  });

  it("disables a model incompatible with the selected Score output and explains why", async () => {
    mount({ outputType: "percentage" });
    await openPicker();
    const span = screen.getByRole("menuitem", { name: /Span-01/ });
    expect(span).toHaveAttribute("aria-disabled", "true");
    expect(span).toHaveTextContent("Span-01 does not support Score");
  });

  it("shows catalog failure without inventing new supported models", async () => {
    const implementation = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, options) =>
      options?.params?.model_type === "decision"
        ? Promise.reject(new Error("catalog unavailable"))
        : implementation(url, options),
    );
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: /^Evaluation model:/ }),
    );
    expect(
      await screen.findByRole("button", { name: "Retry catalog" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /Jev latest/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /GPT-6 Luna Decisions/ }),
    ).not.toBeInTheDocument();
  });
});
