import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";

import EarlyAccessPage from "../EarlyAccessPage";
import { rolloutQueryClient } from "./rollout-fixtures";
import { ShellTestProviders } from "./test-providers";

// RC-05's page part (READINESS 3.13): an owner asks for a product on
// Settings → Early access, sees the request recorded, and sees it enabled once
// staff invite the org. Members see where things stand but cannot ask.
// RC-01: an org with nothing to ask for gets the standard 404 (nothing
// listed, or every listed product already admitted: AUDIT R1, round 2), and
// the page never shows its copy before the server has answered.

const PAGE = "/dashboard/settings/early-access";
const INTRO = /New Omega products open to organizations in stages/;

const row = (product, label, extra = {}) => ({
  product,
  label,
  admitted: false,
  state: null,
  request_status: null,
  requested_at: null,
  ...extra,
});

function renderPage() {
  render(
    <ShellTestProviders
      queryClient={rolloutQueryClient({ earlyAccess: false })}
    >
      <MemoryRouter initialEntries={[PAGE]}>
        <Routes>
          <Route path={PAGE} element={<EarlyAccessPage />} />
          <Route path="/404" element={<div data-testid="page-404" />} />
        </Routes>
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

describe("Settings → Early access", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("an owner asks for access and the row shows the request", async () => {
    const get = vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        can_request: true,
        products: [
          row("hub", "Omega Hub"),
          row("console", "Omega Console", { admitted: true, state: "active" }),
        ],
      },
    });
    const post = vi.spyOn(axios, "post").mockResolvedValue({
      data: {
        can_request: true,
        products: [
          row("hub", "Omega Hub", { request_status: "requested" }),
          row("console", "Omega Console", { admitted: true, state: "active" }),
        ],
      },
    });
    renderPage();
    expect(await screen.findByText("Omega Hub")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/omega-rollout/early-access/");
    expect(screen.getByText(INTRO)).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("early-access-request-hub"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/omega-rollout/early-access/", {
        product: "hub",
      }),
    );
    expect(await screen.findByText("Requested")).toBeInTheDocument();
    expect(
      screen.queryByTestId("early-access-request-hub"),
    ).not.toBeInTheDocument();
  });

  it("the Hub row names no installs: nothing can install an entry yet", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        can_request: true,
        products: [
          row("console", "Omega Console", { admitted: true, state: "active" }),
          row("hub", "Omega Hub"),
        ],
      },
    });
    renderPage();
    expect(await screen.findByText("Omega Hub")).toBeInTheDocument();
    expect(screen.getByTestId("early-access-request-hub")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Agents and skills for Omega, each version approved by a Future AGI maintainer.",
      ),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\binstall/i);
  });

  it("a member sees the status and no request button", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: { can_request: false, products: [row("sandboxes", "Sandboxes")] },
    });
    renderPage();
    expect(await screen.findByText("Sandboxes")).toBeInTheDocument();
    expect(
      screen.getByText(/Only organization owners and admins/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Request access" }),
    ).not.toBeInTheDocument();
  });

  it("with nothing listed for the org it renders the standard 404, not a placeholder", async () => {
    vi.spyOn(axios, "get").mockResolvedValueOnce({
      data: { can_request: true, products: [] },
    });
    renderPage();
    expect(await screen.findByTestId("page-404")).toBeInTheDocument();
    expect(screen.queryByTestId("early-access-page")).toBeNull();
    expect(screen.queryByText(INTRO)).toBeNull();
    expect(screen.queryByText(/no early access programs/)).toBeNull();
  });

  it("an org admitted to every listed product gets the 404, not a waitlist with nothing on it", async () => {
    vi.spyOn(axios, "get").mockResolvedValueOnce({
      data: {
        can_request: true,
        products: [
          row("console", "Omega Console", { admitted: true, state: "active" }),
          row("sandboxes", "Sandboxes", { admitted: true, state: "active" }),
          row("hub", "Omega Hub", { admitted: true, state: "paused" }),
        ],
      },
    });
    renderPage();
    expect(await screen.findByTestId("page-404")).toBeInTheDocument();
    expect(screen.queryByTestId("early-access-page")).toBeNull();
    expect(screen.queryByText(INTRO)).toBeNull();
    expect(screen.queryByText("Enabled")).toBeNull();
  });

  it("a request the org is waiting on keeps the page, with its status", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        can_request: true,
        products: [
          row("console", "Omega Console", { admitted: true, state: "active" }),
          row("hub", "Omega Hub", { request_status: "requested" }),
        ],
      },
    });
    renderPage();
    expect(await screen.findByText("Requested")).toBeInTheDocument();
    expect(screen.getByText(INTRO)).toBeInTheDocument();
    expect(
      screen.queryByTestId("early-access-request-hub"),
    ).not.toBeInTheDocument();
  });

  it("shows no Early access copy while the listing is loading", async () => {
    let answer;
    vi.spyOn(axios, "get").mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    renderPage();
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(screen.queryByText(INTRO)).toBeNull();
    expect(screen.queryByText("Early access")).toBeNull();
    answer({ data: { can_request: true, products: [] } });
    expect(await screen.findByTestId("page-404")).toBeInTheDocument();
  });

  it("a failed load offers a retry and names no product", async () => {
    vi.spyOn(axios, "get").mockRejectedValue(new Error("503"));
    renderPage();
    expect(
      await screen.findByRole(
        "button",
        { name: "Try again" },
        { timeout: 8000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(INTRO)).toBeNull();
    expect(screen.queryByTestId("page-404")).toBeNull();
  });
});
