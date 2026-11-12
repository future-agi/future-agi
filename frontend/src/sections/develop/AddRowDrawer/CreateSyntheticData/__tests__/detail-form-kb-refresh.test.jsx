// Walk flow 44: on "Generate synthetic rows", Create knowledge base opens a new
// tab. The user created a knowledge base there, came back, opened the
// Knowledge base picker and it still said "No option provided". The list was
// read again only on a window focus event, and a browser can bring the tab
// back without sending one. The list is now read again when the tab is shown
// again and each time the picker gets focus, and the empty picker says in
// plain words that it is loading or that there are no knowledge bases yet.
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import DetailForm from "../DetailForm";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const PICKER = "Select knowledge base here";
const NEW_KNOWLEDGE_BASE = { id: "kb-shipping", name: "Shipping policies" };

// What the knowledge base list returns right now.
const server = { knowledgeBases: [] };

function listResponse() {
  return {
    data: {
      result: {
        table_data: server.knowledgeBases.map((knowledgeBase) => ({
          ...knowledgeBase,
        })),
      },
    },
  };
}

function DetailFormInForm() {
  const methods = useForm({
    defaultValues: {
      name: "Shipping questions",
      description: "Customer support questions about shipping",
      useCase: "",
      pattern: "",
      rowNumber: 25,
      kb_id: "",
    },
  });
  return (
    <FormProvider {...methods}>
      <DetailForm
        control={methods.control}
        handleNextTab={() => {}}
        setFocusField={() => {}}
        disabledNext={false}
      />
    </FormProvider>
  );
}

let queryClient;

function renderDetailForm() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <DetailFormInForm />
    </QueryClientProvider>,
  );
}

// The user clicks into the picker: it gets focus, then the click.
async function openPicker() {
  const picker = await screen.findByPlaceholderText(PICKER);
  fireEvent.focus(picker);
  fireEvent.click(picker);
  return picker;
}

beforeEach(() => {
  server.knowledgeBases = [];
  axios.get.mockReset();
  axios.get.mockImplementation(async () => listResponse());
  vi.spyOn(window, "open").mockImplementation(() => ({ opener: window }));
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  queryClient = undefined;
  vi.restoreAllMocks();
});

describe("W44 knowledge base picker after creating one in another tab", () => {
  it("lists a knowledge base made in the other tab on the next open", async () => {
    renderDetailForm();
    await openPicker();
    fireEvent.click(await screen.findByText("Create knowledge base"));
    expect(window.open).toHaveBeenCalledWith("/dashboard/knowledge", "_blank");

    // Made in the other tab. Coming back here sends this tab no focus event.
    server.knowledgeBases = [NEW_KNOWLEDGE_BASE];
    await openPicker();

    expect(
      await screen.findByText(NEW_KNOWLEDGE_BASE.name),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("Shipping questions")).toBeInTheDocument();
  });

  it("reads the list again when this tab is shown again", async () => {
    renderDetailForm();
    await screen.findByPlaceholderText(PICKER);
    expect(axios.get).toHaveBeenCalledTimes(1);

    server.knowledgeBases = [NEW_KNOWLEDGE_BASE];
    const visibility = vi.spyOn(document, "visibilityState", "get");

    visibility.mockReturnValue("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(axios.get).toHaveBeenCalledTimes(1);

    visibility.mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));

    // A click alone, without focus, already finds it.
    fireEvent.click(screen.getByPlaceholderText(PICKER));
    expect(
      await screen.findByText(NEW_KNOWLEDGE_BASE.name),
    ).toBeInTheDocument();
  });

  it("says the list is loading while it is read again", async () => {
    renderDetailForm();
    await screen.findByPlaceholderText(PICKER);

    let finishReading;
    axios.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishReading = resolve;
        }),
    );
    await openPicker();

    expect(
      await screen.findByText("Loading knowledge bases…"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No option provided")).not.toBeInTheDocument();

    server.knowledgeBases = [NEW_KNOWLEDGE_BASE];
    await act(async () => {
      finishReading(listResponse());
    });

    expect(
      await screen.findByText(NEW_KNOWLEDGE_BASE.name),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Loading knowledge bases…"),
    ).not.toBeInTheDocument();
  });

  it("names an empty list plainly", async () => {
    renderDetailForm();
    await openPicker();

    expect(
      await screen.findByText("No knowledge bases yet"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No option provided")).not.toBeInTheDocument();
  });
});
