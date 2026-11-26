import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import axios from "src/utils/axios";
import {
  AGENT_ENVIRONMENTS,
  CODE_AGENT_DETAIL,
  CODE_VERSION_DETAIL,
  HOSTED_AGENT_DETAIL,
  HOSTED_VERSION_DETAIL,
  OBSERVABILITY_REFUSED,
  STORED_AGENT,
} from "src/api/agents/_fixtures/agentDetail";
import AgentDefinitionTab from "../AgentDefinitionTab";

const auth = { role: "Owner" };
vi.mock("../code/CodeCard", () => ({
  default: ({ versionId }) => <div data-testid="code-card">{versionId}</div>,
}));

vi.mock("src/auth/hooks", () => ({ useAuthContext: () => auth }));

const environments = { current: AGENT_ENVIRONMENTS };
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: () => Promise.resolve(environments.current),
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
      storedAgent: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/agent/`,
    },
    project: { getCodeBlockTracer: "/tracer/project/project_sdk_code/" },
  },
}));

vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: () =>
    Promise.resolve([{ id: "p-prod", name: "Harbor production" }]),
}));

vi.mock("react-syntax-highlighter", () => {
  const Prism = ({ children }) => <pre>{children}</pre>;
  Prism.propTypes = { children: PropTypes.node };
  return { Prism };
});

const NOT_STORED = {
  statusCode: 404,
  code: "not_found",
  message: "Agent not stored for this version",
};

// The agent, each version's detail, and the stored Agent of the versions in
// `stored` (any other version answers 404, as the backend does).
const serve = (detail, versions, stored = {}) =>
  axios.get.mockImplementation((url) => {
    const agentOf = url.match(/\/versions\/([^/]+)\/agent\/$/);
    if (agentOf) {
      const body = stored[agentOf[1]];
      return body
        ? Promise.resolve({ data: body })
        : Promise.reject(NOT_STORED);
    }
    const v = Object.entries(versions).find(([id]) =>
      url.endsWith(`/versions/${id}/`),
    );
    if (v) return Promise.resolve({ data: v[1] });
    if (url.endsWith(`/${detail.id}/`))
      return Promise.resolve({ data: detail });
    return Promise.reject(new Error(`unexpected ${url}`));
  });

const renderAt = (agent, search = "") =>
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
      <MemoryRouter
        initialEntries={[
          `/dashboard/agents/connected/${agent.id}/definition${search}`,
        ]}
      >
        <Routes>
          <Route
            path="/dashboard/agents/connected/:agentId/definition"
            element={<AgentDefinitionTab />}
          />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  auth.role = "Owner";
  environments.current = AGENT_ENVIRONMENTS;
  axios.get.mockReset();
  axios.put.mockReset();
});

describe("AgentDefinitionTab", () => {
  it("shows a hosted agent's configuration for the active version", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    expect(await screen.findByText("Configuration")).toBeInTheDocument();
    expect(await screen.findByText("v2")).toBeInTheDocument();
    expect(screen.getByText("Read-only · read from Vapi")).toBeInTheDocument();
    expect(await screen.findByText("sk-a...9f2c")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("lays out Identity, Source and Reach from the version", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    await screen.findByText("sk-a...9f2c");
    ["Identity", "Source", "Reach", "Configuration", "Links"].forEach((t) =>
      expect(screen.getByText(t)).toBeInTheDocument(),
    );
    expect(screen.getByText("Northwind Pharmacy")).toBeInTheDocument();
    // The type under Identity, and the Voice row under Reach.
    expect(screen.getAllByText("Voice")).toHaveLength(2);
    expect(screen.getByText("es")).toBeInTheDocument();
    expect(screen.getByText("Vapi")).toBeInTheDocument();
    expect(
      screen.getByText("Phone call · inbound · caller speaks first"),
    ).toBeInTheDocument();
    expect(screen.getByText("+14155550100")).toBeInTheDocument();
  });

  it("shows the Files card for a code agent, on the version the page shows", async () => {
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(CODE_AGENT_DETAIL);
    expect(await screen.findByTestId("code-card")).toHaveTextContent(
      CODE_VERSION_DETAIL.id,
    );
  });

  it("names a code agent's linked Observe project under Links", async () => {
    const linked = {
      ...CODE_AGENT_DETAIL,
      observe_project: { id: "p-prod", name: "Harbor production" },
    };
    serve(
      linked,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(linked);
    const row = await screen.findByTestId("observe-project-link");
    expect(row).toHaveTextContent("Harbor production · linked");
    expect(
      within(row).getByRole("link", { name: "Open in Observe" }),
    ).toHaveAttribute("href", "/dashboard/observe/p-prod/llm-tracing");
  });

  it("offers Link a project for an unlinked code agent, opening the setup drawer", async () => {
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(CODE_AGENT_DETAIL);
    const row = await screen.findByTestId("observe-project-link");
    expect(row).toHaveTextContent("Not linked");
    fireEvent.click(
      within(row).getByRole("button", { name: "Link a project" }),
    );
    const drawer = await screen.findByRole("presentation");
    expect(
      within(drawer).getByText("Connect production traces"),
    ).toBeInTheDocument();
  });

  it("tells a hosted agent to turn observability on instead of linking", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    const row = await screen.findByTestId("observe-project-link");
    expect(row).toHaveTextContent("turn observability on to pull its calls in");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("won't let a viewer link a project from Definition", async () => {
    auth.role = "workspace_viewer";
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(CODE_AGENT_DETAIL);
    const row = await screen.findByTestId("observe-project-link");
    expect(
      within(row).getByRole("button", { name: "Link a project" }),
    ).toBeDisabled();
  });

  it("links the environments testing the agent", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    const link = await screen.findByRole("link", { name: "ride-voice-agent" });
    expect(
      screen.getByRole("link", { name: "Refund edge cases" }),
    ).toBeInTheDocument();
    fireEvent.click(link);
    expect(currentLocation.pathname).toBe(
      "/dashboard/simulate/environments/e5391b8a",
    );
  });

  it("says when no environment tests the agent yet", async () => {
    environments.current = { ...AGENT_ENVIRONMENTS, results: [] };
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    expect(await screen.findByText("None yet")).toBeInTheDocument();
  });

  it("reads the version picked in ?v=", async () => {
    serve(HOSTED_AGENT_DETAIL, {
      "v1-id": {
        ...HOSTED_VERSION_DETAIL,
        id: "v1-id",
        version_name_display: "v1",
        api_key: null,
      },
      "v2-id": HOSTED_VERSION_DETAIL,
    });
    renderAt(HOSTED_AGENT_DETAIL, "?v=v1-id");
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(await screen.findByText("Not saved")).toBeInTheDocument();
  });

  it("shows a code agent's stored Agent, and who stored it when", async () => {
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(CODE_AGENT_DETAIL);
    expect(
      await screen.findByText("Stored by SDK 0.2.3 on 14-10-2026"),
    ).toBeInTheDocument();
    expect(screen.getByText("Kind")).toBeInTheDocument();
    expect(screen.getByText("create_guest_rider")).toBeInTheDocument();
    expect(
      screen.getByText("github.com/acme/uber-voice-agent"),
    ).toBeInTheDocument();
    expect(screen.getByText("3f9a2c1")).toBeInTheDocument();
    expect(screen.getByText("Phone call · inbound")).toBeInTheDocument();
    expect(screen.getByText("+14155550199")).toBeInTheDocument();
    expect(screen.queryByText("uber_voice_agent")).toBeNull();
    expect(screen.queryByText(/may be out of date/i)).toBeNull();
  });

  it("warns when the stored setup is stale", async () => {
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: { ...STORED_AGENT, setup_stale: true } },
    );
    renderAt(CODE_AGENT_DETAIL);
    expect(
      await screen.findByText(
        "This setup may be out of date. The agent changed after it was stored.",
      ),
    ).toBeInTheDocument();
  });

  it("says when a code agent's version has nothing stored", async () => {
    serve(CODE_AGENT_DETAIL, {
      [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL,
    });
    renderAt(CODE_AGENT_DETAIL);
    expect(
      await screen.findByText("No definition stored for this version"),
    ).toBeInTheDocument();
  });

  it("reads the stored Agent from the Agent endpoint, not the version", async () => {
    serve(
      CODE_AGENT_DETAIL,
      { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL },
      { [CODE_VERSION_DETAIL.id]: STORED_AGENT },
    );
    renderAt(CODE_AGENT_DETAIL);
    await screen.findByText("Stored by SDK 0.2.3 on 14-10-2026");
    expect(axios.get).toHaveBeenCalledWith(
      `/simulate/agent-definitions/${CODE_AGENT_DETAIL.id}/versions/${CODE_VERSION_DETAIL.id}/agent/`,
    );
  });

  it("doesn't ask for a stored Agent when the agent never stored one", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    await screen.findByText("sk-a...9f2c");
    expect(axios.get.mock.calls.some(([u]) => u.endsWith("/agent/"))).toBe(
      false,
    );
  });

  it("shows a hosted agent's configuration when it has nothing stored", async () => {
    const hosted = { ...HOSTED_AGENT_DETAIL, agent_kind: "hosted" };
    serve(hosted, {
      "v2-id": { ...HOSTED_VERSION_DETAIL, agent_kind: "hosted" },
    });
    renderAt(hosted);
    expect(await screen.findByText("sk-a...9f2c")).toBeInTheDocument();
    expect(
      axios.get.mock.calls.some(([u]) => u.endsWith("/v2-id/agent/")),
    ).toBe(true);
  });

  it("shows no Files card for a hosted agent", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    await screen.findByText("Configuration");
    expect(screen.queryByTestId("code-card")).toBeNull();
  });

  it("shows label and value rows while the version loads", async () => {
    axios.get.mockImplementation((url) =>
      url.includes("/versions/")
        ? new Promise(() => {})
        : Promise.resolve({ data: HOSTED_AGENT_DETAIL }),
    );
    renderAt(HOSTED_AGENT_DETAIL);
    // Once the agent is in, Identity, Source and Reach still wait for the
    // version, two rows each, beside Configuration's four.
    await waitFor(() =>
      expect(screen.getAllByTestId("definition-row-skeleton")).toHaveLength(10),
    );
    expect(screen.queryByText("Northwind Pharmacy")).toBeNull();
  });

  it("shows none of the agent's current facts when the version can't load", async () => {
    axios.get.mockImplementation((url) =>
      url.includes("/versions/")
        ? Promise.reject({ message: "Version unavailable" })
        : Promise.resolve({ data: HOSTED_AGENT_DETAIL }),
    );
    renderAt(HOSTED_AGENT_DETAIL);
    expect(
      (await screen.findAllByText("Couldn't load this version")).length,
    ).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText("+14155550100")).toBeNull();
  });

  it("says what observability does while it's off", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    expect(
      await screen.findByText(
        "Turn on to track calls, monitor logs and debug this agent.",
      ),
    ).toBeInTheDocument();
  });

  it("says where the calls go while it's on", async () => {
    serve(
      { ...HOSTED_AGENT_DETAIL, observability_enabled: true },
      { "v2-id": HOSTED_VERSION_DETAIL },
    );
    renderAt(HOSTED_AGENT_DETAIL);
    expect(
      await screen.findByText(
        "Tracking this agent's calls and logs in Observe.",
      ),
    ).toBeInTheDocument();
  });

  it("turns observability on and refetches the agent", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": { ...HOSTED_VERSION_DETAIL } });
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderAt(HOSTED_AGENT_DETAIL);
    const toggle = await screen.findByRole("checkbox", {
      name: "Observability",
    });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${HOSTED_AGENT_DETAIL.id}/edit/`,
        { observability_enabled: true },
      ),
    );
    await waitFor(() =>
      expect(
        axios.get.mock.calls.filter(([u]) =>
          u.endsWith(`/${HOSTED_AGENT_DETAIL.id}/`),
        ).length,
      ).toBeGreaterThan(1),
    );
  });

  it("turns observability on from its label too", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderAt(HOSTED_AGENT_DETAIL);
    await screen.findByRole("checkbox", { name: "Observability" });
    const label = screen.getByText("Observability");
    // Quick clicks on the label shouldn't select its text.
    expect(label.closest("label")).toHaveStyle({ userSelect: "none" });
    fireEvent.click(label);
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${HOSTED_AGENT_DETAIL.id}/edit/`,
        { observability_enabled: true },
      ),
    );
  });

  it("won't let a viewer change observability", async () => {
    auth.role = "workspace_viewer";
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    const toggle = await screen.findByRole("checkbox", {
      name: "Observability",
    });
    expect(toggle).toBeDisabled();
    fireEvent.click(screen.getByText("Observability"));
    expect(axios.put).not.toHaveBeenCalled();
  });

  it("puts the toggle back and shows why when it's refused", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    axios.put.mockRejectedValue(OBSERVABILITY_REFUSED);
    renderAt(HOSTED_AGENT_DETAIL);
    const toggle = await screen.findByRole("checkbox", {
      name: "Observability",
    });
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      OBSERVABILITY_REFUSED.message,
    );
    expect(toggle).not.toBeChecked();
  });

  it("follows the server after a change it accepted", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderAt(HOSTED_AGENT_DETAIL);
    const toggle = await screen.findByRole("checkbox", {
      name: "Observability",
    });
    fireEvent.click(toggle);
    await waitFor(() => expect(axios.put).toHaveBeenCalled());
    // The refetched agent still says false (no observe project yet).
    await waitFor(() => expect(toggle).not.toBeChecked());
  });
});
