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
import { mapAgentDetail } from "src/api/agents/agentDetail";
import {
  AGENT_ENVIRONMENTS,
  CODE_AGENT_DETAIL,
  HOSTED_AGENT_DETAIL,
} from "src/api/agents/_fixtures/agentDetail";
import AgentNextSteps, { observeProjectPath } from "../AgentNextSteps";

const auth = { role: "Owner" };
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => auth }));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
    },
    project: { getCodeBlockTracer: "/tracer/project/project_sdk_code/" },
  },
}));

const projects = { current: [] };
vi.mock("src/api/project/observe-project-list", () => ({
  fetchAllObserveProjects: () => Promise.resolve(projects.current),
}));

const environments = { current: AGENT_ENVIRONMENTS };
vi.mock("src/api/simulate-environments/harnessEnvironments", () => ({
  listHarnessEnvironments: () => Promise.resolve(environments.current),
}));

const enqueueSnackbar = vi.fn();
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar }),
}));

vi.mock("react-syntax-highlighter", () => {
  const Prism = ({ children }) => <pre>{children}</pre>;
  Prism.propTypes = { children: PropTypes.node };
  return { Prism };
});

// Mirrors /tracer/project/project_sdk_code/?project_type=observe.
const SETUP_CODE = {
  installation_guide: {
    Python: "pip install fi-instrumentation-otel",
    TypeScript: "npm install @futureagi/instrumentation",
  },
  keys: {
    Python: 'os.environ["FI_API_KEY"] = "YOUR_FI_API_KEY"',
    TypeScript: 'process.env.FI_API_KEY = "YOUR_FI_API_KEY"',
  },
  project_add_code: {
    Python: 'register(project_name="FUTURE_AGI")',
    TypeScript: 'register({ projectName: "FUTURE_AGI" })',
  },
  instruments: {},
};

const PRODUCTION = { id: "p-prod", name: "Harbor production" };
const STAGING = { id: "p-stage", name: "Harbor staging" };

const codeAgent = (extra = {}) =>
  mapAgentDetail({ ...CODE_AGENT_DETAIL, ...extra });
const hostedAgent = (extra = {}) =>
  mapAgentDetail({ ...HOSTED_AGENT_DETAIL, ...extra });

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderCard = (agent) =>
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
        initialEntries={[`/dashboard/agents/connected/${agent.id}/overview`]}
      >
        <Routes>
          <Route
            path="/dashboard/agents/connected/:agentId/overview"
            element={<AgentNextSteps agent={agent} />}
          />
          <Route path="*" element={null} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const traces = () => screen.getByTestId("next-step-traces");
const environment = () => screen.getByTestId("next-step-environment");

const openDrawer = async () => {
  fireEvent.click(
    within(traces()).getByRole("button", { name: "Connect traces" }),
  );
  return screen.findByRole("presentation");
};

const pick = async (drawer, name) => {
  fireEvent.mouseDown(within(drawer).getByLabelText("Observe project"));
  fireEvent.click(await screen.findByRole("option", { name }));
};

beforeEach(() => {
  auth.role = "Owner";
  projects.current = [PRODUCTION, STAGING];
  environments.current = AGENT_ENVIRONMENTS;
  enqueueSnackbar.mockReset();
  axios.get.mockReset();
  axios.put.mockReset();
  axios.get.mockResolvedValue({ data: { result: SETUP_CODE } });
});

describe("observeProjectPath", () => {
  it("opens the project's tracing page", () => {
    expect(observeProjectPath("p-prod")).toBe(
      "/dashboard/observe/p-prod/llm-tracing",
    );
  });
});

const confirmIn = async (name) => {
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name }));
  return dialog;
};

describe("AgentNextSteps", () => {
  it("shows the two ways forward, side by side", () => {
    renderCard(codeAgent({ environment_count: 0 }));
    expect(screen.getByText("Next steps")).toBeInTheDocument();
    expect(traces()).toHaveTextContent("In production");
    expect(traces()).toHaveTextContent("Get its traces");
    expect(traces()).toHaveTextContent(
      "Choose the Observe project its production calls land in.",
    );
    expect(traces()).toHaveTextContent(
      "Error Feed finds issues in real calls, and Fix can repair them.",
    );
    expect(environment()).toHaveTextContent("In simulation");
    expect(environment()).toHaveTextContent("Build an environment");
    expect(
      within(environment()).getByRole("button", { name: "Build environment" }),
    ).toBeDisabled();
  });

  it("keeps the setup code in a drawer for a code agent", async () => {
    renderCard(codeAgent());
    expect(screen.queryByText("Install dependencies")).toBeNull();
    expect(axios.get).not.toHaveBeenCalled();
    const drawer = await openDrawer();
    expect(
      await within(drawer).findByText("pip install fi-instrumentation-otel"),
    ).toBeInTheDocument();
    ["Install dependencies", "Load API keys", "Set up telemetry"].forEach(
      (title) => expect(within(drawer).getByText(title)).toBeInTheDocument(),
    );
    expect(
      within(drawer).getByText('register(project_name="FUTURE_AGI")'),
    ).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      "/tracer/project/project_sdk_code/",
      { params: { project_type: "observe" } },
    );
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByText("Install dependencies")).toBeNull(),
    );
  });

  it("switches every snippet with one language picker", async () => {
    renderCard(codeAgent());
    const drawer = await openDrawer();
    await within(drawer).findByText("pip install fi-instrumentation-otel");
    expect(
      within(drawer).getAllByRole("button", { name: "TypeScript" }),
    ).toHaveLength(1);
    fireEvent.click(within(drawer).getByRole("button", { name: "TypeScript" }));
    expect(
      within(drawer).getByText("npm install @futureagi/instrumentation"),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText('register({ projectName: "FUTURE_AGI" })'),
    ).toBeInTheDocument();
  });

  it("links the project the user picks, under a label above the field", async () => {
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderCard(codeAgent());
    const drawer = await openDrawer();
    expect(within(drawer).getByText("Observe project").tagName).toBe("LABEL");
    const button = within(drawer).getByRole("button", { name: "Link project" });
    expect(button).toBeDisabled();
    await pick(drawer, "Harbor production");
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${CODE_AGENT_DETAIL.id}/edit/`,
        { production_project_id: "p-prod" },
      ),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Linked to Harbor production",
        { variant: "success" },
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("Install dependencies")).toBeNull(),
    );
  });

  it("shows the server's reason when a link is refused", async () => {
    axios.put.mockRejectedValue({
      response: { data: { detail: "That Observe project was not found." } },
    });
    renderCard(codeAgent());
    const drawer = await openDrawer();
    await pick(drawer, "Harbor staging");
    fireEvent.click(
      within(drawer).getByRole("button", { name: "Link project" }),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "That Observe project was not found.",
        { variant: "error" },
      ),
    );
    expect(
      within(drawer).getByText("Install dependencies"),
    ).toBeInTheDocument();
  });

  it("once linked, names the project and marks the step done", () => {
    renderCard(codeAgent({ observe_project: PRODUCTION }));
    expect(traces()).toHaveTextContent("Harbor production");
    expect(traces()).toHaveTextContent("Done");
    expect(traces()).toHaveTextContent("Error Feed watches its real calls");
    expect(
      within(traces()).getByRole("link", { name: "Open in Observe" }),
    ).toHaveAttribute("href", "/dashboard/observe/p-prod/llm-tracing");
    expect(screen.queryByText("Connect traces")).toBeNull();
  });

  it("opens Change project on the linked project, and starts fresh after closing", async () => {
    renderCard(codeAgent({ observe_project: PRODUCTION }));
    fireEvent.click(
      within(traces()).getByRole("button", { name: "Change project" }),
    );
    const drawer = await screen.findByRole("presentation");
    const input = within(drawer).getByLabelText("Observe project");
    expect(input).toHaveValue("Harbor production");
    expect(
      within(drawer).getByRole("button", { name: "Link project" }),
    ).toBeDisabled();
    await pick(drawer, "Harbor staging");
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByText("Install dependencies")).toBeNull(),
    );
    fireEvent.click(
      within(traces()).getByRole("button", { name: "Change project" }),
    );
    const reopened = await screen.findByRole("presentation");
    expect(within(reopened).getByLabelText("Observe project")).toHaveValue(
      "Harbor production",
    );
  });

  it("asks before unlinking, and leaves the observability switch alone", async () => {
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderCard(codeAgent({ observe_project: PRODUCTION }));
    fireEvent.click(within(traces()).getByRole("button", { name: "Unlink" }));
    expect(axios.put).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Unlink Harbor production?"),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent("simulation traces are not affected");
    await confirmIn("Unlink");
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${CODE_AGENT_DETAIL.id}/edit/`,
        { production_project_id: null },
      ),
    );
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Unlinked from Harbor production",
        { variant: "success" },
      ),
    );
  });

  it("turns observability on for a hosted agent instead of asking for code", async () => {
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderCard(hostedAgent());
    expect(traces()).toHaveTextContent("Enable observability");
    expect(traces()).toHaveTextContent("Its Vapi calls are pulled into");
    expect(screen.queryByText("Connect traces")).toBeNull();
    fireEvent.click(
      within(traces()).getByRole("button", { name: "Turn on observability" }),
    );
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${HOSTED_AGENT_DETAIL.id}/edit/`,
        { observability_enabled: true },
      ),
    );
  });

  it("asks before turning a hosted agent's observability off", async () => {
    axios.put.mockResolvedValue({ data: { message: "ok" } });
    renderCard(hostedAgent({ observe_project: PRODUCTION }));
    expect(traces()).toHaveTextContent("Harbor production");
    expect(traces()).toHaveTextContent("its Vapi calls are pulled in");
    expect(within(traces()).queryByText("Unlink")).toBeNull();
    fireEvent.click(within(traces()).getByRole("button", { name: "Turn off" }));
    expect(axios.put).not.toHaveBeenCalled();
    await confirmIn("Turn off");
    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        `/simulate/agent-definitions/${HOSTED_AGENT_DETAIL.id}/edit/`,
        { observability_enabled: false },
      ),
    );
  });

  it("offers nothing to turn on for a hosted agent with no provider", () => {
    renderCard(hostedAgent({ source: null, provider: null }));
    expect(traces()).toHaveTextContent("Add a provider to this agent first");
    expect(traces()).not.toHaveTextContent("Its — calls");
    expect(
      within(traces()).queryByRole("button", { name: "Turn on observability" }),
    ).toBeNull();
  });

  it("names the environments once one exists and opens the tab", async () => {
    renderCard(codeAgent({ environment_count: 2 }));
    expect(
      await within(environment()).findByText("ride-voice-agent +1 more"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Do either first; the other is here when you need it."),
    ).toBeInTheDocument();
    fireEvent.click(
      within(environment()).getByRole("button", { name: "View environments" }),
    );
    expect(currentLocation.pathname).toBe(
      `/dashboard/agents/connected/${CODE_AGENT_DETAIL.id}/environments`,
    );
  });

  it("drops the hint once both steps are done", () => {
    renderCard(
      codeAgent({ environment_count: 1, observe_project: PRODUCTION }),
    );
    expect(
      screen.queryByText(
        "Do either first; the other is here when you need it.",
      ),
    ).toBeNull();
  });

  it("won't let a viewer connect, change or unlink", () => {
    auth.role = "workspace_viewer";
    renderCard(codeAgent());
    expect(
      within(traces()).getByRole("button", { name: "Connect traces" }),
    ).toBeDisabled();
    renderCard(codeAgent({ observe_project: PRODUCTION }));
    expect(
      screen.getByRole("button", { name: "Change project" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Unlink" })).toBeDisabled();
  });

  it("can't be hidden, so linking stays reachable", () => {
    renderCard(codeAgent());
    expect(screen.queryByRole("button", { name: "Hide" })).toBeNull();
  });

  it("says when the setup code can't be loaded", async () => {
    axios.get.mockRejectedValue({ message: "SDK snippets unavailable" });
    renderCard(codeAgent());
    const drawer = await openDrawer();
    expect(
      await within(drawer).findByText("SDK snippets unavailable"),
    ).toBeInTheDocument();
  });
});
