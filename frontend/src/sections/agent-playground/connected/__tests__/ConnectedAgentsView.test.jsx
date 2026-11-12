import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import { handleError } from "src/utils/queryErrorHandler";
import {
  CONNECTED_AGENTS_FIXTURE,
  connectedAgentsPage,
} from "src/api/agents/_fixtures/connectedAgents";
import ConnectedAgentsView from "../ConnectedAgentsView";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), delete: vi.fn() },
  endpoints: {
    agentDefinitions: {
      list: "/simulate/agent-definitions/",
      delete: "/simulate/agent-definitions/",
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

beforeEach(() => {
  axios.get.mockReset();
  axios.delete.mockReset();
  enqueueSnackbar.mockReset();
  axios.get.mockImplementation((_, { params }) =>
    Promise.resolve({ data: connectedAgentsPage(params) }),
  );
});

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

const renderView = (props = {}) => {
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/agents/connected"]}>
        <ConnectedAgentsView onConnect={vi.fn()} {...props} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
};

const nextPageButton = () => {
  const buttons = screen.getAllByRole("button");
  return buttons[buttons.length - 1];
};

const tickRow = (name) => {
  const row = screen.getByText(name).closest('[role="row"]');
  fireEvent.click(within(row).getByRole("checkbox"));
};

const deleteButton = () => screen.getByRole("button", { name: "Delete" });

const confirmDelete = () => {
  fireEvent.click(deleteButton());
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  return dialog;
};

const pageOf = (rows, { page = 1, limit = 25 }) => ({
  count: rows.length,
  total_pages: Math.max(1, Math.ceil(rows.length / limit)),
  current_page: page,
  results: rows.slice((page - 1) * limit, page * limit),
});

describe("ConnectedAgentsView", () => {
  it("shows the ticket's columns in order and none of the hidden ones", async () => {
    renderView();
    await screen.findByText("Clinic receptionist");
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent.trim());
    expect(headers).toEqual([
      "",
      "Agent name",
      "Modality",
      "Source",
      "Versions",
      "Used in",
      "Last updated",
      "Connected on",
    ]);
    ["Owner", "Production", "Created at", "Connected"].forEach((h) =>
      expect(screen.queryByRole("columnheader", { name: h })).toBeNull(),
    );
  });

  it("renders each cell from what the API sends", async () => {
    renderView();
    await screen.findByText("Clinic receptionist");
    expect(screen.getAllByText("Voice · Inbound").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Voice · Outbound").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Chat").length).toBeGreaterThan(0);
    expect(screen.getByText("Video Call")).toBeInTheDocument();
    expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Upload").length).toBeGreaterThan(0);
    expect(screen.getByText("Vonage Sip")).toBeInTheDocument();
    expect(screen.getAllByText("No environments yet").length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("7 environments")).toBeInTheDocument();
    expect(screen.getByText("ride-voice-agent")).toBeInTheDocument();
    // One-line rows: the description isn't a second line.
    expect(
      screen.queryByText("Inbound phone support for an online storefront"),
    ).toBeNull();
  });

  it("puts the old page's modality icon next to voice and chat", async () => {
    renderView();
    await screen.findByText("Clinic receptionist");
    expect(screen.getByTestId("modality-icon-ca-03")).toBeInTheDocument();
    expect(screen.getByTestId("modality-icon-ca-04")).toBeInTheDocument();
  });

  it("shows an error instead of the empty state when the list fails", async () => {
    axios.get.mockReset();
    axios.get.mockRejectedValue({ message: "Network Error" });
    renderView();
    expect(
      await screen.findByText("Couldn't load connected agents"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No connected agents yet")).toBeNull();
  });

  it("explains how to connect an agent when there are none", async () => {
    axios.get.mockReset();
    axios.get.mockResolvedValue({ data: { count: 0, results: [] } });
    renderView();
    expect(
      await screen.findByText(
        "Connect your agent from a hosted platform, an endpoint, a repository or an upload. Simulations run against a copy of it.",
      ),
    ).toBeInTheDocument();
  });

  it("opens an agent's detail page from its row", async () => {
    renderView();
    fireEvent.click(await screen.findByText("Clinic receptionist"));
    expect(currentLocation.pathname).toBe("/dashboard/agents/connected/ca-03");
  });

  it("asks the server for the next page", async () => {
    renderView();
    await screen.findByText("1–25 of 37");
    fireEvent.click(nextPageButton());
    await screen.findByText("Voice agent 37");
    expect(axios.get).toHaveBeenLastCalledWith("/simulate/agent-definitions/", {
      params: { page: 2, limit: 25 },
      paramsSerializer: expect.any(Object),
    });
  });

  it("pages through the list 25 at a time", async () => {
    renderView();
    await screen.findByText("1–25 of 37");
    expect(screen.getByText("Clinic receptionist")).toBeInTheDocument();

    fireEvent.click(nextPageButton());

    await screen.findByText("26–37 of 37");
    expect(await screen.findByText("Voice agent 37")).toBeInTheDocument();
    expect(screen.queryByText("Clinic receptionist")).toBeNull();
  });

  it("goes back to the first page when the search changes", async () => {
    renderView();
    await screen.findByText("1–25 of 37");
    fireEvent.click(nextPageButton());
    await screen.findByText("26–37 of 37");

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "receptionist" },
    });

    await waitFor(
      () => expect(screen.getByText("1–1 of 1")).toBeInTheDocument(),
      {
        timeout: 2000,
      },
    );
    expect(screen.getByText("Clinic receptionist")).toBeInTheDocument();
  });

  it("says nothing matches a search instead of showing the empty state", async () => {
    renderView();
    await screen.findByText("1–25 of 37");

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: "zzzz" },
    });

    await waitFor(
      () =>
        expect(
          screen.getByText("No agents match your search or filters"),
        ).toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.queryByText("No connected agents yet")).toBeNull();
  });

  it("deletes the ticked agent, not its row number, after a refetch adds a row above", async () => {
    axios.delete.mockResolvedValue({
      data: { agents_updated: 1, versions_updated: 1 },
    });
    const queryClient = renderView();
    await screen.findByText("Clinic receptionist");
    tickRow("Clinic receptionist");
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    axios.get.mockImplementation((_, { params }) => {
      const data = connectedAgentsPage(params);
      return Promise.resolve({
        data: {
          ...data,
          count: data.count + 1,
          results: [
            {
              ...CONNECTED_AGENTS_FIXTURE[4],
              id: "ca-new",
              agent_name: "Brand new",
            },
            ...data.results,
          ],
        },
      });
    });
    await queryClient.invalidateQueries({ queryKey: ["agents", "connected"] });
    await screen.findByText("Brand new");

    fireEvent.click(deleteButton());
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Delete Clinic receptionist?"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "This deletes Clinic receptionist with all its versions.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(axios.delete).toHaveBeenCalledWith(
        "/simulate/agent-definitions/",
        { data: { agent_ids: ["ca-03"] } },
      ),
    );
  });

  it("steps back a page when every agent on the last page is deleted", async () => {
    let rows = CONNECTED_AGENTS_FIXTURE.map((row) => ({
      ...row,
      environment_count: 0,
      environments: [],
    }));
    axios.get.mockImplementation((_, { params }) => {
      const data = pageOf(rows, params);
      if (params.page > data.total_pages) {
        return Promise.reject({ statusCode: 404, message: "Invalid page." });
      }
      return Promise.resolve({ data });
    });
    axios.delete.mockImplementation((_, { data }) => {
      rows = rows.filter((row) => !data.agent_ids.includes(row.id));
      return Promise.resolve({
        data: {
          agents_updated: data.agent_ids.length,
          versions_updated: data.agent_ids.length,
        },
      });
    });
    renderView();
    await screen.findByText("1–25 of 37");
    fireEvent.click(nextPageButton());
    await screen.findByText("26–37 of 37");
    await screen.findByText("Voice agent 37");
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all rows" }));
    expect(screen.getByText("12 selected")).toBeInTheDocument();
    const callsBeforeDelete = axios.get.mock.calls.length;

    confirmDelete();

    await screen.findByText("1–25 of 25");
    expect(screen.getByText("Clinic receptionist")).toBeInTheDocument();
    const pagesAsked = axios.get.mock.calls
      .slice(callsBeforeDelete)
      .map(([, { params }]) => params.page);
    expect(pagesAsked).not.toContain(2);
    expect(enqueueSnackbar).toHaveBeenCalledWith("12 agents deleted.", {
      variant: "success",
    });
  });

  it("will not delete while a ticked agent is still used in an environment", async () => {
    renderView();
    await screen.findByText("Customer support line");
    tickRow("Customer support line");
    tickRow("Clinic receptionist");

    expect(deleteButton()).toBeDisabled();
    expect(
      screen.getByLabelText(
        "Customer support line is still used in environments. Delete those environments first.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the server's refusal once when the delete is refused", async () => {
    const message =
      "Clinic receptionist is tested by 1 environment. Delete those environments first.";
    axios.delete.mockRejectedValue({
      statusCode: 409,
      code: "agent_in_use",
      message,
      result: message,
    });
    renderView();
    await screen.findByText("Clinic receptionist");
    tickRow("Clinic receptionist");

    confirmDelete();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(message, {
        variant: "error",
      }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });
});
