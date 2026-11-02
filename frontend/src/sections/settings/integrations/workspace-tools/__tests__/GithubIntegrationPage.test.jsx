import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import GithubIntegrationPage from "../github/GithubIntegrationPage";
import {
  getIntegrations,
  installGithub,
  resetIntegrations,
} from "../mockStore";

vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));

const renderPage = () =>
  renderWithRouter(<GithubIntegrationPage />, {
    route: "/dashboard/settings/integrations/github",
  });

describe("GitHub integration page", () => {
  beforeEach(() => {
    window.localStorage.clear();
    act(() => resetIntegrations());
  });

  it("offers to install the GitHub App and says what it unlocks", () => {
    renderPage();
    expect(
      screen.getByRole("button", { name: "Install GitHub App" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What connecting GitHub unlocks"),
    ).toBeInTheDocument();
  });

  it("shows every section once installed, with sample agents and PRs", () => {
    act(() => installGithub({ account: "acme-inc", access: "all" }));
    renderPage();
    for (const heading of [
      "Installations",
      "Repositories",
      "Agents connected from GitHub",
      "Pull requests opened by Future AGI",
      "Permissions",
    ]) {
      expect(screen.getByText(heading)).toBeInTheDocument();
    }
    expect(
      screen.getByText("Escalate refunds above $200 before issuing them"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Returns & refunds agent").length,
    ).toBeGreaterThan(0);
  });

  it("finds the agents in a repository when scanned", async () => {
    act(() => installGithub({ account: "acme-inc", access: "all" }));
    renderPage();
    const row = screen
      .getAllByText("acme-inc/support-voice-agent")[0]
      .closest("div");
    fireEvent.click(within(row).getByRole("button", { name: "Scan" }));
    expect(
      await screen.findByText(/^Found \d+ agents/, {}, { timeout: 2000 }),
    ).toBeInTheDocument();
  });

  it("stops a running scan when the page closes", () => {
    vi.useFakeTimers();
    try {
      act(() => installGithub({ account: "acme-inc", access: "all" }));
      const { unmount } = renderPage();
      const row = screen
        .getAllByText("acme-inc/support-voice-agent")[0]
        .closest("div");
      fireEvent.click(within(row).getByRole("button", { name: "Scan" }));
      unmount();
      act(() => vi.advanceTimersByTime(2000));
      expect(
        getIntegrations().scans["acme-inc/support-voice-agent"],
      ).toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends Build environment to the Environments build tab", () => {
    act(() => installGithub({ account: "acme-inc", access: "all" }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Build environment" }));
    expect(window.location.pathname).toBe("/dashboard/simulate/environments");
    expect(window.location.search).toBe("?tab=build");
  });

  it("goes back to not connected after uninstalling the only installation", () => {
    act(() => installGithub({ account: "acme-inc", access: "all" }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Uninstall",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Install GitHub App" }),
    ).toBeInTheDocument();
  });
});
