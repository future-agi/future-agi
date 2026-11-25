import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "src/utils/test-utils";
import AgentValuesTab from "../AgentValuesTab";

const auth = { role: "Owner" };
const route = {
  agentId: "agent-1",
  agent: { id: "agent-1", activeVersionId: "v2", activeVersionNumber: 2 },
};
const state = {};
const mutate = vi.fn();
const snackbar = vi.fn();

vi.mock("../useAgentRoute", () => ({ default: () => route }));
vi.mock("src/api/agents/agentDetail", async () => ({
  ...(await vi.importActual("src/api/agents/agentDetail")),
  useAgentVersionValues: (...args) => {
    state.asked = args;
    return state.values;
  },
  useChangeVersion: () => ({ mutate, isPending: false }),
}));
vi.mock("src/api/agent-definition/agent-definition-version", () => ({
  useAgentDefinitionVersions: () => state.versions,
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => auth }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: snackbar }),
}));

const loaded = (names, extra = {}) => ({
  isLoading: false,
  data: {
    names,
    missingRequired: [],
    madeByEnvironment: ["AGENT_API_TOKEN", "agent_api_token"],
    ...extra,
  },
});

beforeEach(() => {
  auth.role = "Owner";
  route.agent = {
    id: "agent-1",
    activeVersionId: "v2",
    activeVersionNumber: 2,
  };
  mutate.mockReset();
  snackbar.mockReset();
  state.versions = { data: { pages: [{ results: [] }] } };
  state.values = loaded([
    { name: "AGENT_API_TOKEN", required: false, madeByEnvironment: true },
    { name: "DB_URL", required: true, madeByEnvironment: false },
  ]);
});

describe("AgentValuesTab", () => {
  it("lists the production version's names and marks a generated one", () => {
    render(<AgentValuesTab />);

    expect(state.asked).toEqual(["agent-1", "v2"]);
    expect(screen.getByText("DB_URL")).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(
      screen.getByText("Made by the test environment"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/refused while the agent has one of its own/),
    ).toBeInTheDocument();
  });

  it("removes a generated value as one new version", () => {
    render(<AgentValuesTab />);
    const row = screen.getByTestId("agent-value-AGENT_API_TOKEN");
    fireEvent.click(row.querySelector("button"));

    expect(screen.getByText("Removes AGENT_API_TOKEN")).toBeInTheDocument();
    expect(
      screen.getByText(/prepared again before its next run/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save values" }));

    expect(mutate).toHaveBeenCalledWith(
      { kind: "credentials", remove_names: ["AGENT_API_TOKEN"] },
      expect.any(Object),
    );
  });

  it("warns before a required value is removed and after the save", () => {
    render(<AgentValuesTab />);
    const row = screen.getByTestId("agent-value-DB_URL");
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);

    expect(row).toHaveTextContent("builds and runs are refused");
    fireEvent.click(screen.getByRole("button", { name: "Save values" }));
    act(() =>
      mutate.mock.calls[0][1].onSuccess({
        created: true,
        version: { version_name_display: "v3" },
        missing_required_names: ["DB_URL"],
      }),
    );

    expect(snackbar).toHaveBeenCalledWith(
      "Builds and runs are refused until DB_URL has a value",
      { variant: "warning" },
    );
  });

  it("changes a value without showing it", () => {
    render(<AgentValuesTab />);
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.change(screen.getByLabelText("New value for DB_URL"), {
      target: { value: "pg://new" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));

    expect(screen.getByText("Changes DB_URL")).toBeInTheDocument();
    expect(screen.queryByText(/pg:\/\/new/)).not.toBeInTheDocument();
    expect(screen.queryByText(/prepared again/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save values" }));
    expect(mutate.mock.calls[0][0]).toEqual({
      kind: "credentials",
      secret_values: { DB_URL: "pg://new" },
    });
  });

  it("refuses a new value under a name the environment makes", () => {
    render(<AgentValuesTab />);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "agent_api_token" },
    });
    fireEvent.change(screen.getByLabelText("Value"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      screen.getByText(/makes this value itself; yours isn't needed/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Save values")).not.toBeInTheDocument();
  });

  it("holds the save while another version is still being checked", () => {
    state.versions = {
      data: {
        pages: [
          {
            results: [
              {
                id: "v3",
                version_number: 3,
                version_name_display: "v3",
                preflight_status: "pending",
              },
            ],
          },
        ],
      },
    };
    render(<AgentValuesTab />);
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);

    expect(screen.getByText(/v3 is still being checked/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save values" })).toBeDisabled();
  });

  it("offers a provider key a change, never a removal", () => {
    state.values = loaded([
      { name: "VAPI_API_KEY", required: false, providerKey: true },
    ]);
    render(<AgentValuesTab />);

    expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("names the required values the agent has none for", () => {
    state.values = loaded([], { missingRequired: ["DB_URL", "KEY"] });
    render(<AgentValuesTab />);

    expect(
      screen.getByText(/needs a value for DB_URL, KEY/),
    ).toBeInTheDocument();
  });

  it("is read-only for a viewer and for an agent an environment manages", () => {
    auth.role = "Viewer";
    const view = render(<AgentValuesTab />);
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    view.unmount();

    auth.role = "Owner";
    route.agent = {
      ...route.agent,
      managedBy: { id: "e1", name: "Support env" },
    };
    render(<AgentValuesTab />);
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.getByText(/change its values from/)).toBeInTheDocument();
  });
});
