import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import axios from "src/utils/axios";
import {
  CODE_AGENT_DETAIL,
  HOSTED_AGENT_DETAIL,
} from "src/api/agents/_fixtures/agentDetail";
import AgentDetailLayout from "../AgentDetailLayout";
import AgentDetailIndexRedirect from "../AgentDetailIndexRedirect";
import { agentDetailPath, resolveVersionId } from "../agentDetailTabs";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn() },
  endpoints: {
    agentDefinitions: {
      detail: (id) => `/simulate/agent-definitions/${id}/`,
      versionDetail: (id, v) =>
        `/simulate/agent-definitions/${id}/versions/${v}/`,
      edit: (id) => `/simulate/agent-definitions/${id}/edit/`,
      versions: (id) => `/simulate/agent-definitions/${id}/versions/`,
    },
  },
}));

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const Tab = ({ name }) => <div data-testid={`tab-${name}`} />;
Tab.propTypes = { name: PropTypes.string.isRequired };

// Mirrors the connected/:agentId block in routes/sections/dashboard.jsx.
const renderAt = (url) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/agents/connected/:agentId"
              element={<AgentDetailLayout />}
            >
              <Route index element={<AgentDetailIndexRedirect />} />
              <Route path="overview" element={<Tab name="overview" />} />
              <Route path="definition" element={<Tab name="definition" />} />
              <Route path="versions" element={<Tab name="versions" />} />
              <Route
                path="environments"
                element={<Tab name="environments" />}
              />
            </Route>
            <Route path="*" element={<div data-testid="elsewhere" />} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  return client;
};

// The agent detail, its paginated versions list (newest first, `pageSize`
// per page) and each version's own detail.
const serve = (detail, { pageSize = 10 } = {}) => {
  const all = [...(detail.versions ?? [])].sort(
    (a, b) => b.version_number - a.version_number,
  );
  axios.get.mockImplementation((url, config) => {
    if (!url.includes(detail.id)) {
      return Promise.reject(new Error(`unexpected ${url}`));
    }
    if (url.endsWith("/versions/")) {
      const page = config.params.page;
      const results = all.slice((page - 1) * pageSize, page * pageSize);
      return Promise.resolve({
        data: {
          count: all.length,
          next: page * pageSize < all.length ? "next" : null,
          current_page: page,
          results,
        },
      });
    }
    const one = all.find((v) => url.endsWith(`/versions/${v.id}/`));
    if (one) return Promise.resolve({ data: one });
    return Promise.resolve({ data: detail });
  });
};

const HOSTED = HOSTED_AGENT_DETAIL;
const CODE = CODE_AGENT_DETAIL;
const base = (a) => `/dashboard/agents/connected/${a.id}`;

beforeEach(() => {
  axios.get.mockReset();
});

describe("agentDetailTabs", () => {
  it("takes ?v= as given, else the active version", () => {
    expect(resolveVersionId("v1", "v2")).toBe("v1");
    expect(resolveVersionId(null, "v2")).toBe("v2");
    expect(resolveVersionId(null, null)).toBeNull();
  });

  it("builds tab paths", () => {
    expect(agentDetailPath("a1")).toBe(
      "/dashboard/agents/connected/a1/overview",
    );
    expect(agentDetailPath("a1", "definition")).toBe(
      "/dashboard/agents/connected/a1/definition",
    );
  });
});

describe("AgentDetailLayout", () => {
  it("opens the bare agent URL on Overview", async () => {
    serve(HOSTED);
    renderAt(base(HOSTED));
    await screen.findByTestId("tab-overview");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/overview`);
  });

  it("shows the hosted agent's header", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByRole("heading", { name: "Northwind Pharmacy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Vapi")).toBeInTheDocument();
    const subline = screen.getByTestId("agent-detail-subline");
    expect(subline).toHaveTextContent(HOSTED.assistant_id);
    expect(subline).toHaveTextContent("Created 13-10-2026");
    expect(subline).toHaveTextContent("No environments");
  });

  it("shows GitHub and the environment count for a code agent", async () => {
    serve(CODE);
    renderAt(`${base(CODE)}/overview`);
    const subline = await screen.findByTestId("agent-detail-subline");
    expect(subline).toHaveTextContent("GitHub");
    expect(subline).toHaveTextContent("1 environment");
  });

  it("keeps the header and tabs in place while the agent loads", async () => {
    axios.get.mockImplementation(() => new Promise(() => {}));
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByTestId("agent-header-skeleton"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByTestId("tab-overview")).toBeInTheDocument();
  });

  it("goes back to Connected agents", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected");
  });

  it.each([
    ["hosted", HOSTED],
    ["code", CODE],
  ])("gives a %s agent both Versions and Environments", async (_, agent) => {
    serve(agent);
    renderAt(`${base(agent)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    expect(screen.getByRole("tab", { name: /^Versions/ })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /^Environments/ }),
    ).toBeInTheDocument();
  });

  it("shows Constraints, Improvements and Connections as disabled Coming soon tabs", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("tab", { name: "Overview" });
    ["Constraints", "Improvements", "Connections"].forEach((name) => {
      const tab = screen.getByRole("tab", { name: new RegExp(name) });
      expect(tab).toBeDisabled();
      expect(within(tab).getByLabelText("Coming soon")).toBeInTheDocument();
    });
  });

  it("opens Environments on a hosted agent", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/environments`);
    await screen.findByTestId("tab-environments");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/environments`);
  });

  it("counts versions from version_count, not the loaded list", async () => {
    serve({ ...HOSTED, version_count: 12 });
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByRole("tab", { name: "Versions 12" }),
    ).toBeInTheDocument();
  });

  it("shows the version selector on the active version", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    const select = await screen.findByRole("combobox");
    await waitFor(() => expect(select).toHaveTextContent("v2 · active"));
  });

  it("writes the picked version to ?v= and clears it for the active one", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /^v1/ }));
    await waitFor(() => expect(currentLocation.search).toBe("?v=v1-id"));
    expect(screen.getByRole("combobox")).toHaveTextContent("Viewing v1");

    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /^v2/ }));
    await waitFor(() => expect(currentLocation.search).toBe(""));
  });

  it("lists versions from the paginated versions list", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    await screen.findByRole("option", { name: /^v1/ });
    expect(axios.get).toHaveBeenCalledWith(
      `/simulate/agent-definitions/${HOSTED.id}/versions/`,
      { params: { page: 1, limit: 10 } },
    );
  });

  it("pins a picked version that isn't on the first page to the top of the menu", async () => {
    serve(HOSTED, { pageSize: 1 });
    renderAt(`${base(HOSTED)}/overview?v=v1-id`);
    const select = await screen.findByRole("combobox");
    await waitFor(() => expect(select).toHaveTextContent("Viewing v1"));
    fireEvent.mouseDown(screen.getByRole("combobox"));
    await waitFor(() =>
      expect(
        screen.getAllByRole("option").map((o) => o.textContent.slice(0, 2)),
      ).toEqual(["v1", "v2"]),
    );
  });

  it("keeps the picked version when switching tabs", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview?v=v1-id`);
    fireEvent.click(await screen.findByRole("tab", { name: "Definition" }));
    await screen.findByTestId("tab-definition");
    expect(currentLocation.search).toBe("?v=v1-id");
  });

  it("switches tabs by path", async () => {
    serve(HOSTED);
    renderAt(`${base(HOSTED)}/overview`);
    fireEvent.click(await screen.findByRole("tab", { name: "Definition" }));
    await screen.findByTestId("tab-definition");
    expect(currentLocation.pathname).toBe(`${base(HOSTED)}/definition`);
  });

  it("keeps the page when a background refetch fails", async () => {
    serve(HOSTED);
    const client = renderAt(`${base(HOSTED)}/overview`);
    await screen.findByRole("heading", { name: "Northwind Pharmacy" });
    axios.get.mockRejectedValue({ message: "Server error" });
    await act(async () => {
      await client.refetchQueries();
    });
    await waitFor(() =>
      expect(client.getQueryState(["agents", "detail", HOSTED.id]).status).toBe(
        "error",
      ),
    );
    expect(
      screen.getByRole("heading", { name: "Northwind Pharmacy" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Server error")).toBeNull();
  });

  it("shows the server's message when the agent can't be loaded", async () => {
    axios.get.mockRejectedValue({ message: "Agent definition not found" });
    renderAt(`${base(HOSTED)}/overview`);
    expect(
      await screen.findByText("Agent definition not found"),
    ).toBeInTheDocument();
  });
});
