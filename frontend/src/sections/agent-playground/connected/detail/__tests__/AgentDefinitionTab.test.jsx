import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import axios from "src/utils/axios";
import {
  CODE_AGENT_DETAIL,
  CODE_VERSION_DETAIL,
  HOSTED_AGENT_DETAIL,
  HOSTED_VERSION_DETAIL,
  OBSERVABILITY_REFUSED,
} from "src/api/agents/_fixtures/agentDetail";
import AgentDefinitionTab from "../AgentDefinitionTab";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
    },
  },
}));

const serve = (detail, versions) =>
  axios.get.mockImplementation((url) => {
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
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  axios.get.mockReset();
  axios.put.mockReset();
});

describe("AgentDefinitionTab", () => {
  it("shows a hosted agent's configuration for the active version", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    expect(await screen.findByText("What we read")).toBeInTheDocument();
    expect(await screen.findByText("· v2")).toBeInTheDocument();
    expect(await screen.findByText("sk-a...9f2c")).toBeInTheDocument();
    expect(
      screen.getByText("Inbound · caller speaks first"),
    ).toBeInTheDocument();
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
    expect(await screen.findByText("· v1")).toBeInTheDocument();
    expect(await screen.findByText("Not saved")).toBeInTheDocument();
  });

  it("shows a code agent's stored Agent as rows only", async () => {
    serve(CODE_AGENT_DETAIL, { [CODE_VERSION_DETAIL.id]: CODE_VERSION_DETAIL });
    renderAt(CODE_AGENT_DETAIL);
    expect(await screen.findByText("Source")).toBeInTheDocument();
    expect(screen.queryByText(/Stored by SDK/)).toBeNull();
    expect(screen.queryByText("uber_voice_agent")).toBeNull();
    expect(screen.queryByText(/may be out of date/i)).toBeNull();
  });

  it("warns when the stored setup is stale", async () => {
    serve(CODE_AGENT_DETAIL, {
      [CODE_VERSION_DETAIL.id]: {
        ...CODE_VERSION_DETAIL,
        agent_details: {
          ...CODE_VERSION_DETAIL.agent_details,
          setup_stale: true,
        },
      },
    });
    renderAt(CODE_AGENT_DETAIL);
    expect(
      await screen.findByText(
        "This setup may be out of date. The agent changed after it was stored.",
      ),
    ).toBeInTheDocument();
  });

  it("says when a code agent's version has nothing stored", async () => {
    serve(CODE_AGENT_DETAIL, {
      [CODE_VERSION_DETAIL.id]: { ...CODE_VERSION_DETAIL, agent_details: null },
    });
    renderAt(CODE_AGENT_DETAIL);
    expect(
      await screen.findByText("No definition stored for this version"),
    ).toBeInTheDocument();
  });

  it("previews File structure under a Coming soon note", async () => {
    serve(HOSTED_AGENT_DETAIL, { "v2-id": HOSTED_VERSION_DETAIL });
    renderAt(HOSTED_AGENT_DETAIL);
    const region = await screen.findByRole("region", {
      name: "File structure",
    });
    expect(
      within(region).getByText(/Browse the files we read from your agent/),
    ).toBeInTheDocument();
    expect(within(region).getByLabelText("Coming soon")).toBeInTheDocument();
    expect(within(region).getByTestId("coming-soon-preview")).toHaveTextContent(
      "agent.yaml",
    );
  });

  it("shows label and value rows while the version loads", async () => {
    axios.get.mockImplementation((url) =>
      url.includes("/versions/")
        ? new Promise(() => {})
        : Promise.resolve({ data: HOSTED_AGENT_DETAIL }),
    );
    renderAt(HOSTED_AGENT_DETAIL);
    expect(
      await screen.findAllByTestId("definition-row-skeleton"),
    ).toHaveLength(4);
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
