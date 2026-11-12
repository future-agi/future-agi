import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { agentDetailQueryKey } from "src/api/agents/agentDetail";
import AgentReadingState from "../AgentReadingState";

const state = { list: { data: { pages: [{ results: [] }] } } };

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => state.list,
}));
vi.mock("src/api/agents/agentConnect", () => ({
  useFoundCheck: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));

const agent = { id: "agent-1", foundCheck: { required: false, doneAt: null } };

const version = (preflight_status) => ({
  id: "v1",
  version_number: 1,
  version_name_display: "v1",
  preflight_status,
  check_step: preflight_status === "pending" ? "reading" : null,
});

const show = (client) =>
  render(
    <QueryClientProvider client={client}>
      <AgentReadingState agent={agent} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  state.list = { data: { pages: [{ results: [version("pending")] }] } };
});

describe("AgentReadingState", () => {
  it("refreshes the agent once its reading finishes", () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const view = show(client);
    expect(invalidate).not.toHaveBeenCalled();

    state.list = { data: { pages: [{ results: [version("passed")] }] } };
    view.rerender(
      <QueryClientProvider client={client}>
        <AgentReadingState agent={agent} />
      </QueryClientProvider>,
    );

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: agentDetailQueryKey("agent-1"),
    });
  });

  it("leaves an agent whose version was already checked alone", () => {
    state.list = { data: { pages: [{ results: [version("passed")] }] } };
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    show(client);

    expect(invalidate).not.toHaveBeenCalled();
  });
});
