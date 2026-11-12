import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const PATH = "/dashboard/hub/:publisher/:name/:tab?";
const DRAFT_ONLY = "THE PUBLISHER DRAFT TEXT";

function routes(entry, { me = meUser, meStatus = 200, parts = [] } = {}) {
  return [
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, versions],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", me, meStatus],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\//,
      { ...entry.publisher, entryCount: 3 },
    ],
  ];
}

const page = (at = "/dashboard/hub/future-agi/first-agent") =>
  renderAt(<HubEntryPage />, { path: PATH, at });

// The installs panel shows once there is an install path (the Hub CLI here);
// cliCopy.test.jsx covers the page without one.
const withInstallPath = () => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  return { parts: [OMEGA_PART.HUB_CLI] };
};
// Opens are counted through a "Use this" handoff; with none shipped the
// panel has no opens line (lib/installs.js).
const withOpenPath = () => {
  vi.stubEnv("VITE_OMEGA_HUB_OPEN_STUDIO", "1");
  return withInstallPath();
};
afterEach(() => vi.unstubAllEnvs());

describe("Entry page (SPEC §7.2)", () => {
  it("shows the confirmed disclosure under Confirmed in review, never the draft", async () => {
    // The public API never sends the draft; even if a manifest carried one,
    // the page reads only `disclosure`.
    const entry = structuredClone(official);
    entry.latest.manifest.disclosure = {
      summary: DRAFT_ONLY,
      shell: DRAFT_ONLY,
    };
    respond(routes(entry));
    page();
    const panel = await screen.findByTestId("hub-disclosure");
    expect(
      within(panel).getByTestId("hub-disclosure-confirmed"),
    ).toHaveTextContent("Confirmed in review");
    expect(panel).toHaveTextContent(official.latest.disclosure.summary);
    expect(panel).toHaveTextContent(official.latest.disclosure.shell);
    expect(document.body).not.toHaveTextContent(DRAFT_ONLY);
  });

  it("says Derived from for a remix, and shows the unranked notice", async () => {
    const entry = {
      ...structuredClone(official),
      remixOf: "future-agi/first-agent@1.0.0",
      ranked: false,
      unrankedReason: "quality",
    };
    respond(routes(entry));
    page();
    expect(await screen.findByTestId("hub-derived-from")).toHaveTextContent(
      "Derived from future-agi/first-agent",
    );
    expect(screen.getByTestId("hub-unranked-notice")).toHaveTextContent(
      "Not ranked: quality",
    );
  });

  // Each count and its definition show once there is one, so
  // the entry here has been liked, installed and opened.
  it("prints each count's definition from the rules file", async () => {
    respond(
      routes(
        { ...structuredClone(official), likes: 3, installs30d: 4, opens30d: 2 },
        withOpenPath(),
      ),
    );
    page();
    await screen.findByTestId("hub-installs");
    await waitFor(() =>
      expect(
        screen.getByTestId("hub-count-definition-installs"),
      ).toHaveAttribute("aria-label", rules.counts.installs),
    );
    expect(screen.getByTestId("hub-count-definition-opens")).toHaveAttribute(
      "aria-label",
      rules.counts.opens,
    );
    expect(screen.getByTestId("hub-count-definition-likes")).toHaveAttribute(
      "aria-label",
      rules.counts.likes,
    );
  });

  // A zero beside a real count is a calm line, never "—";
  // with nothing counted the panel is left out (hubFirstVisit.test.jsx).
  it('says "No installs yet", with no "—", when opens were counted and installs were not', async () => {
    respond(
      routes(
        { ...structuredClone(official), installs30d: 0, opens30d: 3 },
        withOpenPath(),
      ),
    );
    page();
    const panel = await screen.findByTestId("hub-installs");
    expect(within(panel).getByTestId("hub-opens-value")).toHaveTextContent("3");
    expect(within(panel).queryByTestId("hub-installs-value")).toBeNull();
    expect(within(panel).getByText("No installs yet")).toBeInTheDocument();
    expect(panel.textContent).not.toContain("—");
  });

  it('disables Like with "Sign in to like" when there is no session', async () => {
    respond(
      routes(structuredClone(official), {
        me: { error: { code: "OMEGA_HUB_UNAUTHENTICATED" } },
        meStatus: 401,
      }),
    );
    page();
    const like = await screen.findByTestId("hub-like-button");
    await waitFor(() => expect(like).toBeDisabled());
    expect(like).toHaveTextContent("Sign in to like");
  });

  // Report a problem sits in the title block's More menu; it opens the
  // private report form, and the page has no other text box.
  it("links Discord and offers a private Report, with no on-page conversation", async () => {
    respond(routes(structuredClone(official)));
    page();
    expect(await screen.findByTestId("hub-discord-link")).toHaveAttribute(
      "href",
      "https://discord.com/invite/n2tCUKBkAw",
    );
    expect(document.querySelectorAll("textarea").length).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    const report = await screen.findByTestId("hub-report-button");
    expect(report).toHaveTextContent("Report a problem");
    fireEvent.click(report);
    const dialog = await screen.findByTestId("hub-report-dialog");
    expect(dialog).toHaveTextContent(
      "goes only to Future AGI maintainers. It is never shown on the Hub.",
    );
  });

  it("renders the tombstone on a 410", async () => {
    respond([
      [
        "get",
        ENTRY,
        {
          error: { code: "OMEGA_HUB_GONE" },
          tombstone: {
            ref: "future-agi/first-agent",
            category: "security issue",
            at: "2026-10-14T09:00:00Z",
          },
        },
        410,
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    page();
    const tomb = await screen.findByTestId("hub-tombstone");
    expect(tomb).toHaveTextContent("Removed by Future AGI: security issue.");
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
  });

  it("follows a {movedTo} answer to the new entry URL", async () => {
    respond([
      [
        "get",
        "/omega-hub/v1/entries/someone/old-name",
        { movedTo: "someone/new-name" },
        301,
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    page("/dashboard/hub/someone/old-name");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/hub/someone/new-name",
      ),
    );
  });

  it("follows a redirect the browser already took (the answer names another reference)", async () => {
    respond([
      [
        "get",
        "/omega-hub/v1/entries/someone/old-name",
        { ...structuredClone(official), ref: "future-agi/first-agent" },
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    page("/dashboard/hub/someone/old-name/files");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/hub/future-agi/first-agent/files",
      ),
    );
  });
});
