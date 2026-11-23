import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { agentDetailQueryKey } from "src/api/agents/agentDetail";
import AgentReadingState from "../AgentReadingState";

const state = { list: { data: { pages: [{ results: [] }] } } };
const auth = { role: "Owner" };
const changeMutate = vi.fn();
const retryMutate = vi.fn();
const snackbar = vi.fn();

vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => state.list,
}));
vi.mock("src/api/agents/agentDetail", async () => ({
  ...(await vi.importActual("src/api/agents/agentDetail")),
  useChangeVersion: () => ({ mutate: changeMutate, isPending: false }),
  useRetryVersion: () => ({ mutate: retryMutate, isPending: false }),
}));
vi.mock("src/api/agents/agentConnect", () => ({
  useFoundCheck: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => auth,
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: snackbar }),
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

const listOf = (...rows) => ({ data: { pages: [{ results: rows }] } });

const hoursAgo = (hours) =>
  new Date(Date.now() - hours * 3600 * 1000).toISOString();

const credentialsMissing = {
  ...version("failed"),
  check_failure: {
    code: "credentials_missing",
    message: "Missing credentials: ANTHROPIC_API_KEY, SEARCH_TOKEN",
    retryable: false,
    missing: ["ANTHROPIC_API_KEY", "SEARCH_TOKEN"],
  },
};

beforeEach(() => {
  auth.role = "Owner";
  changeMutate.mockReset();
  retryMutate.mockReset();
  snackbar.mockReset();
  state.list = { data: { pages: [{ results: [version("pending")] }] } };
});

describe("AgentReadingState", () => {
  it("refreshes the agent and its Definition tab once its reading finishes", () => {
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
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["agents", "version", "agent-1"],
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["agents", "stored-agent", "agent-1"],
    });
  });

  it("leaves an agent whose version was already checked alone", () => {
    state.list = { data: { pages: [{ results: [version("passed")] }] } };
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    show(client);

    expect(invalidate).not.toHaveBeenCalled();
  });

  it("offers Retry on a check pending past the server's wait limit", () => {
    state.list = listOf({ ...version("pending"), created_at: hoursAgo(2) });
    show(new QueryClient());

    expect(screen.getByTestId("agent-reading-state")).toHaveTextContent(
      "taking longer than it should",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retryMutate).toHaveBeenCalledWith(
      { versionId: "v1" },
      expect.any(Object),
    );
  });

  it("dates a stalled check from its latest start, not its creation", () => {
    state.list = listOf({
      ...version("pending"),
      created_at: hoursAgo(3),
      check_started_at: hoursAgo(0.1),
    });
    show(new QueryClient());

    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("offers no Retry while a check is still within the wait limit", () => {
    state.list = listOf({ ...version("pending"), created_at: hoursAgo(0.5) });
    show(new QueryClient());

    expect(screen.getByTestId("agent-reading-state")).toHaveTextContent(
      "Reading v1",
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("asks an editor for the credentials the first check found missing", () => {
    state.list = listOf(credentialsMissing);
    show(new QueryClient());

    expect(screen.getByTestId("agent-credentials-form")).toBeInTheDocument();
    expect(screen.getByLabelText("ANTHROPIC_API_KEY")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByLabelText("SEARCH_TOKEN")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("saves the missing credentials as a credentials change", () => {
    state.list = listOf(credentialsMissing);
    show(new QueryClient());
    const save = screen.getByRole("button", { name: "Save credentials" });

    fireEvent.change(screen.getByLabelText("ANTHROPIC_API_KEY"), {
      target: { value: "sk-ant-1" },
    });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("SEARCH_TOKEN"), {
      target: { value: "tok-2" },
    });
    fireEvent.click(save);

    expect(changeMutate).toHaveBeenCalledWith(
      {
        kind: "credentials",
        secret_values: { ANTHROPIC_API_KEY: "sk-ant-1", SEARCH_TOKEN: "tok-2" },
      },
      expect.any(Object),
    );
    act(() =>
      changeMutate.mock.calls[0][1].onSuccess({
        version: { version_name_display: "v2" },
      }),
    );
    expect(snackbar).toHaveBeenCalledWith(
      "v2 created with these credentials and is being checked",
      { variant: "success" },
    );
  });

  it("shows a read-only member the missing credentials without a form", () => {
    auth.role = "workspace_viewer";
    state.list = listOf(credentialsMissing);
    show(new QueryClient());

    expect(screen.getByTestId("agent-reading-state")).toHaveTextContent(
      "ANTHROPIC_API_KEY",
    );
    expect(screen.queryByTestId("agent-credentials-form")).toBeNull();
  });
});
