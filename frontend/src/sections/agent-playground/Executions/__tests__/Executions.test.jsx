import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import Executions from "../Executions";

const { detail } = vi.hoisted(() => ({ detail: vi.fn() }));
vi.mock("../ExecutionDetailView", () => ({
  default: ({ graphId, executionId }) => {
    detail(graphId, executionId);
    return <output>{executionId}</output>;
  },
}));

const row = (id, status = "success") => ({
  id,
  status,
  started_at: "2026-10-23T12:34:00Z",
});
const page = (executions, next = null) => ({
  data: { result: { executions, metadata: { next_page: next } } },
});
const key = (id) => ["agent-playground", "graph-executions", id];

function openHistory() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const pages = {
    a: [page([row("a-latest")], 2), page([row("a-older", "failed")])],
    b: [page([row("b-latest")])],
  };
  const request = vi.spyOn(axios, "get").mockImplementation((url, config) => {
    const id = Object.keys(pages).find(
      (agent) => endpoints.agentPlayground.graphExecutions(agent) === url,
    );
    if (!id) throw new Error(`Unexpected request: ${url}`);
    return Promise.resolve(pages[id][config.params.page_number - 1]);
  });
  client.setQueryData(key("b"), { pages: pages.b, pageParams: [1] });
  detail.mockClear();
  window.history.replaceState({}, "", "/history/a");
  const view = render(
    <QueryClientProvider client={client}>
      <Link to="/history/a">Agent A</Link>
      <Link to="/history/b">Agent B</Link>
      <Routes>
        <Route path="/history/:agentId" element={<Executions />} />
      </Routes>
    </QueryClientProvider>,
  );
  return {
    client,
    pages,
    request,
    close() {
      view.unmount();
      client.clear();
      request.mockRestore();
    },
  };
}

describe("execution history", () => {
  it("keeps a run selected from page two when a refetch prepends a newer run", async () => {
    const history = openHistory();
    try {
      await screen.findByText("a-latest");
      // Fire the native scroll event on the scrollable list, not the row.
      let el = screen.getByText("Success");
      while (el && getComputedStyle(el).overflowY !== "auto")
        el = el.parentElement;
      expect(el).not.toBeNull();
      fireEvent.scroll(el);
      fireEvent.click(await screen.findByText("Failed"));
      await screen.findByText("a-older");
      history.pages.a[0] = page([row("a-new", "running"), row("a-latest")], 2);
      await act(() => history.client.invalidateQueries({ queryKey: key("a") }));
      await screen.findByText("Running");
      expect(screen.getByRole("status")).toHaveTextContent("a-older");
    } finally {
      history.close();
    }
  });

  it("selects the new agent's newest run without requesting stale detail on a cached switch", async () => {
    const history = openHistory();
    try {
      await screen.findByText("a-latest");
      fireEvent.click(screen.getByText("Agent B"));
      await screen.findByText("b-latest");
      expect(detail).not.toHaveBeenCalledWith("b", "a-latest");
      fireEvent.click(screen.getByText("Agent A"));
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("a-latest"),
      );
      expect(detail).not.toHaveBeenCalledWith("a", "b-latest");
    } finally {
      history.close();
    }
  });
});
