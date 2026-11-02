import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

const mockNavigate = vi.fn();
const mockCreateGraph = vi.fn();
let mockCanCreate = true;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useCreateGraph: () => ({ mutate: mockCreateGraph, isPending: false }),
}));

vi.mock("../hooks/useCanEditAgent", () => ({
  default: () => ({ canCreate: mockCanCreate }),
}));

vi.mock("src/api/harness/harness", () => ({
  preflightHarnessJob: vi.fn(),
  storeHarnessSecretValues: vi.fn(),
  createHarnessJob: vi.fn(),
  harnessIdempotencyKey: () => "idem-test",
  getHarnessJob: vi.fn(),
  listHarnessJobs: vi.fn(),
  uploadHarnessSource: vi.fn(),
}));

const { default: ConnectAgentPage } = await import("../ConnectAgentPage");

const theme = createTheme({ palette: palette("light") });

const renderPage = ({ entries = ["/dashboard/agents/connect"] } = {}) =>
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ThemeProvider theme={theme}>
          <MemoryRouter
            initialEntries={entries}
            initialIndex={entries.length - 1}
          >
            <ConnectAgentPage />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );

const tab = (name) => screen.getByRole("tab", { name: new RegExp(name) });
const connect = (name) =>
  fireEvent.click(screen.getByRole("button", { name: `Connect ${name}` }));

beforeEach(() => {
  mockNavigate.mockReset();
  mockCreateGraph.mockReset();
  mockCanCreate = true;
});

describe("ConnectAgentPage", () => {
  it("is the New agent page with only the ways to connect", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: "New agent" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Connect it from where it runs today, or build one here.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Search ways to connect" }),
    ).toBeInTheDocument();
    expect(tab("Code")).toHaveAttribute("aria-selected", "true");
    expect(tab("Hosted platforms")).toBeInTheDocument();
    expect(tab("Other ways")).toBeInTheDocument();
    expect(
      screen.getByRole("listitem", { name: "GitHub" }),
    ).toBeInTheDocument();
    for (const text of [
      "Build environment",
      "Use a prebuilt world",
      "Build from code",
      "Build from scratch",
      "Your agents",
    ]) {
      expect(screen.queryByText(text)).toBeNull();
    }
    expect(screen.queryByText(/MCP server/)).toBeNull();
  });

  it("opens a way's form in the page, and back finds the list on the same tab", () => {
    renderPage();
    fireEvent.click(tab("Hosted platforms"));
    connect("Vapi");

    expect(screen.getByRole("heading", { name: "Vapi" })).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Paste the key")[0]).toBeVisible();
    expect(screen.queryByRole("tab", { name: /Hosted platforms/ })).toBeNull();
    // One way back: the header's arrow, which now returns to the list.
    expect(screen.getAllByRole("button", { name: /^Back/ })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "New agent" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back to New agent" }));
    expect(
      screen.getByRole("heading", { name: "New agent" }),
    ).toBeInTheDocument();

    expect(screen.queryByRole("heading", { name: "Vapi" })).toBeNull();
    expect(tab("Hosted platforms")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("listitem", { name: "Vapi" })).toBeInTheDocument();
  });

  it("says when a way is UI only beside its title", () => {
    renderPage();
    connect("GitLab");
    const header = screen.getByRole("heading", {
      name: "GitLab",
    }).parentElement;
    expect(within(header).getByLabelText("UI only")).toBeInTheDocument();
  });

  it("builds one in the Playground from the line beside the tabs", () => {
    renderPage();
    expect(screen.getByText(/No agent yet\?/)).toHaveTextContent(
      "No agent yet? Build one in Future AGI. Write its prompt, pick a model, add tools.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Build one in Future AGI" }),
    );
    expect(mockCreateGraph).toHaveBeenCalledTimes(1);
  });

  it("leaves out Build one in Future AGI without permission to create agents", () => {
    mockCanCreate = false;
    renderPage();
    expect(screen.queryByText(/No agent yet\?/)).toBeNull();
  });

  it("goes back to the page it came from", () => {
    renderPage({
      entries: ["/dashboard/agents/playground", "/dashboard/agents/connect"],
    });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(mockNavigate).toHaveBeenCalledWith(-1);
  });

  it("goes back to Connected agents when opened directly", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/agents/connected");
  });
});
