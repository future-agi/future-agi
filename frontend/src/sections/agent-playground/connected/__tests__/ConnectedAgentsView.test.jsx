import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import axios from "src/utils/axios";
import { connectedAgentsPage } from "src/api/agents/_fixtures/connectedAgents";
import ConnectedAgentsView from "../ConnectedAgentsView";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    agentDefinitions: { list: "/simulate/agent-definitions/" },
  },
}));

beforeEach(() => {
  axios.get.mockReset();
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
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/agents/connected"]}>
        <ConnectedAgentsView onConnect={vi.fn()} {...props} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const nextPageButton = () => {
  const buttons = screen.getAllByRole("button");
  return buttons[buttons.length - 1];
};

describe("ConnectedAgentsView", () => {
  it("shows the ticket's columns in order and none of the hidden ones", async () => {
    renderView();
    await screen.findByText("Clinic receptionist");
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent.trim());
    expect(headers).toEqual([
      "Agent name",
      "Modality",
      "Source",
      "Versions",
      "Used in",
      "Created at",
    ]);
    ["Owner", "Production", "Last updated", "Connected"].forEach((h) =>
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
    expect(
      screen.getByText("Inbound phone support for an online storefront"),
    ).toBeInTheDocument();
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
          screen.getByText("No agents match your search"),
        ).toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(screen.queryByText("No connected agents yet")).toBeNull();
  });
});
