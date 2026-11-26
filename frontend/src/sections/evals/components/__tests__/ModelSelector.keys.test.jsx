/* eslint-disable react/prop-types */
import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import ModelSelector from "../ModelSelector";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";

vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
  useFeatureAllowed: () => ({ allowed: false }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/form-code-editor", () => ({
  FormCodeEditor: () => null,
}));
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/Actions", () => ({
  default: ({ onEditClick }) => <button onClick={onEditClick}>Edit key</button>,
}));

let saved;
const provider = (hasKey = false) => ({
  provider: "typesafe",
  display_name: "TypeSafe",
  type: "text",
  has_key: hasKey,
  masked_key: hasKey ? "****" : null,
});

beforeEach(() => {
  vi.clearAllMocks();
  saved = false;
  axios.get.mockImplementation(async (url) => {
    if (url === endpoints.develop.apiKey.status) {
      return { data: { result: { providers: [provider(saved)] } } };
    }
    if (url === endpoints.develop.modelList) {
      return { data: { results: [], next: null, current_page: 1 } };
    }
    return { data: { results: [], result: { table_data: [], total_rows: 0 } } };
  });
  axios.post.mockImplementation(async () => {
    saved = true;
    return { data: { status: true } };
  });
});

function Editor() {
  const [model, setModel] = useState("custom-model");
  const [mode, setMode] = useState("auto");
  return (
    <>
      <output data-testid="selected-model">{model}</output>
      <ModelSelector
        model={model}
        onModelChange={setModel}
        mode={mode}
        onModeChange={setMode}
      />
    </>
  );
}
const renderWithQueries = (child) =>
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
      {child}
    </QueryClientProvider>,
  );

async function requestJev(label) {
  await userEvent.click(
    screen.getByRole("button", { name: /Evaluation model:/ }),
  );
  await userEvent.click(await screen.findByRole("menuitem", { name: label }));
  await screen.findByText("Configure API keys");
}
async function saveKey() {
  await userEvent.type(
    await screen.findByLabelText("API Key"),
    "test-typesafe-key",
  );
  await userEvent.click(screen.getByRole("button", { name: "save" }));
}

describe("Pending evaluator selection through the real key drawer", () => {
  it.each([
    ["Jev latest", "jev-latest"],
    ["Jev 1.13 (pinned)", "jev-1.13.0"],
  ])(
    "uses the requested %s immediately after a successful key save",
    async (label, model) => {
      renderWithQueries(<Editor />);
      await requestJev(new RegExp(label.replace(/[().]/g, "\\$&")));
      expect(
        screen.getByRole("button", {
          name: "Evaluation model: custom-model",
          hidden: true,
        }),
      ).toBeInTheDocument();
      await saveKey();
      expect(axios.post).toHaveBeenCalledWith(
        endpoints.develop.apiKey.create,
        expect.objectContaining({ provider: "typesafe" }),
      );
      expect(
        await screen.findByRole("button", {
          name: `Evaluation model: ${label}`,
        }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Evaluation mode: Quick" }),
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(
          screen.queryByText("Configure API keys"),
        ).not.toBeInTheDocument(),
      );
      expect(screen.getByTestId("selected-model")).toHaveTextContent(model);
    },
  );

  it("keeps the original model and mode after a failed key save", async () => {
    axios.post.mockRejectedValue(new Error("Key rejected"));
    renderWithQueries(<Editor />);
    await requestJev(/Jev latest/);
    await saveKey();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "save" })).toBeEnabled(),
    );
    expect(
      screen.getByRole("button", {
        name: "Evaluation model: custom-model",
        hidden: true,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Evaluation mode: Auto",
        hidden: true,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Configure API keys")).toBeInTheDocument();
  });

  it("does not apply a pending model after cancelling while the save is in flight", async () => {
    let finishSave;
    axios.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishSave = resolve;
        }),
    );
    renderWithQueries(<Editor />);
    await requestJev(/Jev latest/);
    await saveKey();
    await waitFor(() => expect(finishSave).toBeTypeOf("function"));
    await userEvent.click(
      screen.getByRole("button", { name: "Close API key configuration" }),
    );
    await act(async () => {
      finishSave({ data: { status: true } });
    });
    await waitFor(() =>
      expect(screen.queryByText("Configure API keys")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Evaluation model: custom-model" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Evaluation mode: Auto" }),
    ).toBeInTheDocument();
  });
});

it("notifies key-update success with only the provider, never the credential", async () => {
  const onKeySaved = vi.fn();
  renderWithQueries(
    <KeyCard
      data={{ ...provider(true), hasKey: true, maskedKey: "****" }}
      onKeySaved={onKeySaved}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Edit key" }));
  await saveKey();
  await waitFor(() =>
    expect(onKeySaved).toHaveBeenCalledExactlyOnceWith("typesafe"),
  );
  expect(axios.post).toHaveBeenCalledWith(
    endpoints.develop.apiKey.update,
    expect.objectContaining({ provider: "typesafe" }),
  );
});
