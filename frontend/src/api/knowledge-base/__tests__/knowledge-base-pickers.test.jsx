// Knowledge-base pickers list every knowledge base of the organization.
//
// The fake server below answers the way the backend does. The table route
// (/model-hub/knowledge-base/get/) takes only search, sort, page_number and
// page_size, refuses any other query field with a 400, and returns 10 rows
// when no page_size is sent. The picker route (/model-hub/knowledge-base/list/)
// returns every knowledge base as {id, name}, with no pages; with status it
// keeps only the knowledge bases that finished indexing.
//
// Each picker below calls the shared list hook with its own arguments, so a
// picker that sends a field the backend refuses, or reads only the first page,
// shows fewer than all twelve.
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios from "src/utils/axios";
import DetailForm from "src/sections/develop/AddRowDrawer/CreateSyntheticData/DetailForm";
import { useKnowledgeBaseList } from "../files";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn() },
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const TABLE_ROUTE = "/model-hub/knowledge-base/get/";
const PICKER_ROUTE = "/model-hub/knowledge-base/list/";
const TABLE_QUERY_FIELDS = ["search", "sort", "page_number", "page_size"];
const COMPLETED = "Completed";

// Twelve knowledge bases, newest first, the order both routes return.
const KNOWLEDGE_BASES = Array.from({ length: 12 }, (_, index) => {
  const number = String(12 - index).padStart(2, "0");
  return { id: `kb-${number}`, name: `Policies ${number}`, status: COMPLETED };
});
const ALL_NAMES = KNOWLEDGE_BASES.map((knowledgeBase) => knowledgeBase.name);
// The two made first: past the first ten rows of the table route.
const OLDEST_NAMES = ["Policies 01", "Policies 02"];

const server = { knowledgeBases: KNOWLEDGE_BASES, requests: [] };

function matching(search) {
  const text = String(search ?? "").toLowerCase();
  return server.knowledgeBases.filter((knowledgeBase) =>
    knowledgeBase.name.toLowerCase().includes(text),
  );
}

function answer(url, params, result) {
  server.requests.push({ url, params, status: 200 });
  return Promise.resolve({ data: { status: true, result } });
}

function refuse(url, params, status, result) {
  server.requests.push({ url, params, status });
  const error = new Error(`Request failed with status code ${status}`);
  error.statusCode = status;
  error.response = { status, data: { status: false, result } };
  return Promise.reject(error);
}

function fakeGet(url, config) {
  const params = config?.params ?? {};
  if (url === TABLE_ROUTE) {
    const unknown = Object.keys(params).filter(
      (key) => !TABLE_QUERY_FIELDS.includes(key),
    );
    if (unknown.length) {
      return refuse(
        url,
        params,
        400,
        Object.fromEntries(unknown.map((key) => [key, ["Unknown field."]])),
      );
    }
    const pageSize = Number(params.page_size ?? 10);
    const pageNumber = Number(params.page_number ?? 0);
    const rows = matching(params.search);
    return answer(url, params, {
      table_data: rows
        .slice(pageNumber * pageSize, (pageNumber + 1) * pageSize)
        .map(({ id, name, status }) => ({
          id,
          name,
          files_uploaded: 1,
          status,
          error: null,
          created_by: "Test User",
        })),
      total_rows: rows.length,
    });
  }
  if (url === PICKER_ROUTE) {
    let rows = matching(params.search);
    if (params.status) {
      rows = rows.filter((knowledgeBase) => knowledgeBase.status === COMPLETED);
    }
    return answer(url, params, {
      table_data: rows.map(({ id, name }) => ({ id, name })),
    });
  }
  return refuse(url, params, 404, "Not found.");
}

// What every picker does with the list: one option per knowledge base.
// eslint-disable-next-line react/prop-types
function KnowledgeBasePicker({ args }) {
  const { data } = useKnowledgeBaseList(...args);
  const options = (data || []).map(({ id, name }) => ({
    label: name,
    value: id,
  }));
  return (
    <ul aria-label="Knowledge base options">
      {options.map((option) => (
        <li key={option.value}>{option.label}</li>
      ))}
    </ul>
  );
}

let queryClient;

function renderWithClient(ui) {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

const optionNames = () =>
  screen.queryAllByRole("listitem").map((item) => item.textContent);

const refusedRequests = () =>
  server.requests.filter((request) => request.status !== 200);

beforeEach(() => {
  server.knowledgeBases = KNOWLEDGE_BASES;
  server.requests = [];
  axios.get.mockReset();
  axios.get.mockImplementation(fakeGet);
  vi.spyOn(window, "open").mockImplementation(() => ({ opener: window }));
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  queryClient = undefined;
  vi.restoreAllMocks();
});

// Each picker and the arguments it passes to the list hook.
const PICKERS = [
  ["the evaluation drawer's mapping form", ["", null, { status: true }]],
  ["the eval details config tab", ["", null, { status: true }]],
  ["the eval playground input section", ["", null, { status: true }]],
  ["the agent details form", ["", null]],
  ["the new agent behaviour step", ["", null]],
  ["the generate rows dialog", ["", { enabled: true }]],
  ["the synthetic data drawer", ["", { enabled: true }]],
  ["the synthetic data detail form", ["", null]],
  ["the synthetic data view", ["", null]],
  ["the synthetic summary drawer", ["", null]],
  ["the replay sessions variable mapping", ["", null]],
  ["the knowledge base page switcher", []],
  ["the knowledge base rename dialog", []],
  ["the knowledge base page search", ["polic"]],
];

describe("knowledge base pickers in an organization with twelve knowledge bases", () => {
  it.each(PICKERS)(
    "%s lists all twelve with a request the backend accepts",
    async (_picker, args) => {
      renderWithClient(<KnowledgeBasePicker args={args} />);

      await waitFor(() => expect(optionNames()).toHaveLength(12));
      expect(optionNames()).toEqual(ALL_NAMES);
      expect(server.requests.length).toBeGreaterThan(0);
      expect(refusedRequests()).toEqual([]);
    },
  );

  it("an evaluation picker leaves out a knowledge base still being indexed", async () => {
    server.knowledgeBases = [
      { id: "kb-indexing", name: "Policies draft", status: "Processing" },
      ...KNOWLEDGE_BASES,
    ];
    renderWithClient(<KnowledgeBasePicker args={["", null, { status: true }]} />);

    await waitFor(() => expect(optionNames()).toHaveLength(12));
    expect(optionNames()).toEqual(ALL_NAMES);
    expect(optionNames()).not.toContain("Policies draft");
    expect(refusedRequests()).toEqual([]);
  });

  it("a search narrows the knowledge base page search to the names that match", async () => {
    renderWithClient(<KnowledgeBasePicker args={["Policies 1"]} />);

    await waitFor(() => expect(optionNames()).toHaveLength(3));
    expect(optionNames()).toEqual(["Policies 12", "Policies 11", "Policies 10"]);
    expect(refusedRequests()).toEqual([]);
  });
});

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

describe("the synthetic data form's knowledge base picker", () => {
  it("offers all twelve, the two made first included", async () => {
    renderWithClient(<DetailFormInForm />);
    const picker = await screen.findByPlaceholderText(
      "Select knowledge base here",
    );
    fireEvent.focus(picker);
    fireEvent.click(picker);

    for (const name of OLDEST_NAMES) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
    ALL_NAMES.forEach((name) => {
      expect(screen.getByText(name)).toBeInTheDocument();
    });
    expect(refusedRequests()).toEqual([]);
  });
});
