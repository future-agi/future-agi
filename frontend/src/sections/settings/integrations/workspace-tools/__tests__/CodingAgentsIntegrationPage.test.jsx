import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { SnackbarProvider } from "notistack";
import { renderWithRouter } from "src/utils/test-utils";
import CodingAgentsIntegrationPage from "../coding-agents/CodingAgentsIntegrationPage";
import {
  connectCloudAgent,
  connectCodingAgent,
  getIntegrations,
  resetIntegrations,
} from "../mockStore";

vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));

const renderPage = () =>
  renderWithRouter(
    <SnackbarProvider>
      <CodingAgentsIntegrationPage />
    </SnackbarProvider>,
    { route: "/dashboard/settings/integrations/coding-agents" },
  );

const rowOf = (label) =>
  screen.getByText(label, { selector: "p, span" }).closest("[data-agent-row]");

describe("Coding agents integration page", () => {
  beforeEach(() => {
    window.localStorage.clear();
    act(() => resetIntegrations());
  });

  it("lists the four cloud agents, each ready to connect", () => {
    renderPage();
    for (const label of [
      "Claude Code on the web",
      "Codex cloud",
      "Cursor background agents",
      "Devin",
    ]) {
      expect(
        within(rowOf(label)).getByRole("button", { name: "Connect" }),
      ).toBeInTheDocument();
    }
  });

  it("connects a cloud agent by secret reference and shows recent hand-offs", async () => {
    renderPage();
    fireEvent.click(
      within(rowOf("Codex cloud")).getByRole("button", { name: "Connect" }),
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), {
      target: { value: "vault://acme/coding-agents/codex_cloud_api_key" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(
      await within(rowOf("Codex cloud")).findByText(
        /Connected for the workspace/,
        {},
        { timeout: 2500 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Recent hand-offs")).toBeInTheDocument();
    expect(
      screen.getByText("Refunds above $200 issued without a supervisor"),
    ).toBeInTheDocument();
  });

  it("gives Claude Code the plugin install command and the MCP config", () => {
    renderPage();
    fireEvent.click(
      within(rowOf("Claude Code")).getByRole("button", { name: "Connect" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(/claude plugin install future-agi/),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByText("MCP server"));
    expect(within(dialog).getByText(/\.mcp\.json/)).toBeInTheDocument();
  });

  it("asks before disconnecting a cloud agent", () => {
    act(() =>
      connectCloudAgent("codex_cloud", {
        keyRef: "vault://acme/coding-agents/codex_cloud_api_key",
      }),
    );
    renderPage();
    fireEvent.click(
      within(rowOf("Codex cloud")).getByRole("button", {
        name: "More actions",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Disconnect" }));
    expect(getIntegrations().cloudAgents.codex_cloud).toBeDefined();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Disconnect",
      }),
    );
    expect(getIntegrations().cloudAgents.codex_cloud).toBeUndefined();
  });

  it("keeps the default header when several of your agents are connected", () => {
    act(() => {
      connectCodingAgent("claude_code", "plugin");
      connectCodingAgent("codex", "mcp");
    });
    renderPage();
    expect(
      screen.getByText(
        "Let Claude Code, Codex, Cursor or a cloud agent fix issues. Future AGI checks every change.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Yours:/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0);
  });

  it("shows what the agent can never do", () => {
    renderPage();
    expect(screen.getByText("What it can never do")).toBeInTheDocument();
    expect(screen.getByText("Grade its own candidate")).toBeInTheDocument();
  });
});
