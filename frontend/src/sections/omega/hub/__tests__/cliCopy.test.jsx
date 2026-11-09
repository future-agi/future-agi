import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import HubPublisherPage from "../HubPublisherPage";
import HubReviewQueuePage from "../HubReviewQueuePage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import UploadCard from "../components/UploadCard";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// Pin the dotenv-free local API fallback so the copied registry is deterministic.
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// HIDE-UNFINISHED-FA fix round 1: no Hub page names the `omega hub` CLI, or
// the CLI at all, until the server says a customer can install it
// (OMEGA_PART HUB_CLI). Each case renders the real page with no part
// released and reads the whole document, then shows the same page with
// HUB_CLI, so the gate (not a deletion) is what hides it.

const CLI_TEXT = /omega hub|\bCLI\b|terminal/i;
const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const PATH = "/dashboard/hub/:publisher/:name/:tab?";

function entryRoutes(entry, parts) {
  return [
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, versions],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", meUser],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\//,
      { ...entry.publisher, entryCount: 3 },
    ],
  ];
}

const openEntry = () =>
  renderAt(<HubEntryPage />, {
    path: PATH,
    at: "/dashboard/hub/future-agi/first-agent",
  });

// Let /api/capabilities/ answer, so a gate that would open has had its chance.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

// The page's own words: everything but the README, which is the publisher's
// content (the official fixture's README shows `omega hub run`; the official
// entries' text is HUB-OPS's, see the lane README).
function pageCopy() {
  const copy = document.body.cloneNode(true);
  copy
    .querySelectorAll('[data-testid="hub-readme"]')
    .forEach((node) => node.remove());
  return copy.textContent;
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Entry page without the Hub CLI", () => {
  it("shows no CLI command, no Use this and no always-empty installs panel", async () => {
    respond(entryRoutes(structuredClone(official), []));
    openEntry();
    await screen.findByTestId("hub-publisher-card");
    await settle();
    expect(screen.queryByTestId("hub-remixes")).toBeNull();
    expect(screen.queryByTestId("hub-use-this")).toBeNull();
    expect(screen.queryByTestId("hub-installs")).toBeNull();
    expect(screen.getByTestId("hub-readme")).toBeInTheDocument();
    expect(pageCopy()).not.toMatch(CLI_TEXT);
  });

  it("keeps the lineage of a remix, without the terminal line", async () => {
    const entry = { ...structuredClone(official), remixCount: 2 };
    respond(entryRoutes(entry, []));
    openEntry();
    const block = await screen.findByTestId("hub-remixes");
    expect(within(block).getByTestId("hub-remix-count")).toHaveTextContent(
      "Remixes (2)",
    );
    await settle();
    expect(block.textContent).not.toMatch(CLI_TEXT);
  });

  it("keeps the installs panel when something was really counted", async () => {
    respond(entryRoutes({ ...structuredClone(official), installs30d: 4 }, []));
    openEntry();
    expect(await screen.findByTestId("hub-installs-value")).toHaveTextContent(
      "4",
    );
  });

  it("gives a yanked version's reason, and no install claim, without the CLI", async () => {
    const entry = structuredClone(official);
    entry.latest = {
      ...entry.latest,
      status: "yanked",
      yankedReason: "bad prompt",
    };
    respond(entryRoutes(entry, []));
    openEntry();
    const notice = await screen.findByTestId("hub-yanked-notice");
    await settle();
    // Round 2: nothing installs any version before the CLI ships, so the
    // notice no longer says "It still installs by its exact version."
    expect(notice).toHaveTextContent("Yanked: bad prompt");
    expect(notice.textContent).not.toMatch(CLI_TEXT);
    expect(notice.textContent).not.toMatch(/install/i);
  });

  it("says what happened to a removed entry, with no install line, without the CLI", async () => {
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
      [
        "get",
        ENTRY,
        {
          error: { code: "OMEGA_HUB_GONE" },
          tombstone: {
            ref: "future-agi/first-agent",
            category: "security issue",
          },
        },
        410,
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    openEntry();
    expect(await screen.findByTestId("hub-tombstone")).toHaveTextContent(
      "Removed by Future AGI: security issue.",
    );
    await settle();
    // Round 2: no version was ever installable before the CLI ships, so the
    // tombstone claims no lost install either.
    expect(screen.queryByTestId("hub-tombstone-installs")).toBeNull();
    expect(document.body.textContent).not.toMatch(CLI_TEXT);
    expect(document.body.textContent).not.toMatch(/install/i);
    expect(document.body.textContent).not.toContain("`");
  });
});

describe("Entry page once the Hub CLI ships", () => {
  it("offers the remix command, Use this and the installs panel", async () => {
    respond(entryRoutes(structuredClone(official), [OMEGA_PART.HUB_CLI]));
    openEntry();
    const block = await screen.findByTestId("hub-remixes");
    // CopyCommand splits a long command for its middle ellipsis (HUB-LAUNCH-SAFETY) and keeps the whole one as the title.
    expect(
      await within(block).findByTitle(
        "omega hub new my-first-agent --from hub:future-agi/first-agent@1.0.0 --registry http://localhost:8000/omega-hub/v1",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Remix it from your terminal:"),
    ).toBeInTheDocument();
    expect(await screen.findByTestId("hub-use-this")).toBeInTheDocument();
    expect(screen.getByTestId("hub-installs")).toBeInTheDocument();
  });

  it("names `omega hub outdated` on a removed entry, as code", async () => {
    respond([
      [
        "get",
        "/api/capabilities/",
        capabilitiesAnswer({ parts: [OMEGA_PART.HUB_CLI] }),
      ],
      [
        "get",
        ENTRY,
        {
          error: { code: "OMEGA_HUB_GONE" },
          tombstone: {
            ref: "future-agi/first-agent",
            category: "security issue",
          },
        },
        410,
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    openEntry();
    const line = await screen.findByTestId("hub-tombstone-installs");
    await waitFor(() =>
      expect(line.querySelector("code")).toHaveTextContent(
        "omega hub outdated",
      ),
    );
    expect(line.textContent).not.toContain("`");
  });

  it("says the CLI warns on a yanked version", async () => {
    const entry = structuredClone(official);
    entry.latest = { ...entry.latest, status: "yanked", yankedReason: null };
    respond(entryRoutes(entry, [OMEGA_PART.HUB_CLI]));
    openEntry();
    const notice = await screen.findByTestId("hub-yanked-notice");
    await waitFor(() =>
      expect(notice).toHaveTextContent(
        "Yanked. It still installs by its exact version; the CLI warns.",
      ),
    );
  });
});

describe("Publishing and review without the Hub CLI", () => {
  it("the upload card names the checks and the review, not the CLI", () => {
    renderAt(
      <UploadCard publishers={[{ handle: "c-test", displayName: "C Test" }]} />,
    );
    const card = screen.getByTestId("hub-upload-card");
    expect(card).toHaveTextContent(
      "It is checked automatically, then a Future AGI maintainer reviews it.",
    );
    expect(card.textContent).not.toMatch(CLI_TEXT);
  });

  it("an empty submissions list offers the upload, not the terminal", async () => {
    respond([
      [
        "get",
        "/api/capabilities/",
        capabilitiesAnswer({ parts: [OMEGA_PART.HUB_PUBLISH] }),
      ],
      ["get", "/omega-hub/v1/submissions", []],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubSubmissionsPage />);
    expect(
      await screen.findByText(
        "Upload a folder. You will follow it here through checks and review.",
      ),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(CLI_TEXT);
  });

  it("an empty submissions list names the terminal once the CLI ships", async () => {
    respond([
      [
        "get",
        "/api/capabilities/",
        capabilitiesAnswer({
          parts: [OMEGA_PART.HUB_PUBLISH, OMEGA_PART.HUB_CLI],
        }),
      ],
      ["get", "/omega-hub/v1/submissions", []],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubSubmissionsPage />);
    expect(
      await screen.findByText(
        "Publish from your terminal or upload a folder. You will follow it here through checks and review.",
      ),
    ).toBeInTheDocument();
  });

  it("the review queue offers no CLI channel, and a ?channel=cli link reads any channel", async () => {
    const maintainer = { ...meUser, isMaintainer: true };
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
      ["get", "/omega-hub/v1/me", maintainer],
      ["get", /^\/omega-hub\/v1\/review\/queue/, { count: 0, results: [] }],
    ]);
    renderAt(<HubReviewQueuePage />, {
      at: "/dashboard/hub/review?channel=cli",
    });
    expect(await screen.findByText("The queue is empty")).toBeInTheDocument();
    await settle();
    const asked = calls.filter(
      (call) => call.path === "/omega-hub/v1/review/queue",
    );
    expect(asked.length).toBeGreaterThan(0);
    // The link's channel=cli is not offered, so the queue is not narrowed by it.
    expect(asked.map((call) => call.params?.channel)).not.toContain("cli");
    const channel = screen
      .getAllByRole("combobox")
      .find((box) =>
        box.closest(".MuiFormControl-root")?.textContent.includes("Channel"),
      );
    fireEvent.mouseDown(channel);
    const options = (await screen.findAllByRole("option")).map(
      (option) => option.textContent,
    );
    expect(options).toEqual(["Any channel", "Web upload", "GitHub"]);
    expect(document.body.textContent).not.toMatch(/\bCLI\b/);
  });
});

describe("Publisher tier badge", () => {
  const profile = (entryCount) => ({
    handle: "c-test",
    displayName: "C Test",
    tier: "community",
    entryCount,
    likes: 0,
    installs30d: 0,
    firstPublishedAt: null,
  });

  it("claims no review for a community publisher with nothing listed", async () => {
    respond([
      ["get", "/omega-hub/v1/publishers/c-test", profile(0)],
      [
        "get",
        /^\/omega-hub\/v1\/publishers\/c-test\/entries/,
        { count: 0, results: [] },
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubPublisherPage />, {
      path: "/dashboard/hub/publishers/:handle",
      at: "/dashboard/hub/publishers/c-test",
    });
    expect(await screen.findByTestId("hub-publisher")).toHaveTextContent(
      "C Test",
    );
    expect(screen.queryByTestId("hub-tier-badge")).toBeNull();
    expect(document.body).not.toHaveTextContent("Community reviewed");
  });

  it("shows Community reviewed once a version is listed", async () => {
    respond([
      ["get", "/omega-hub/v1/publishers/c-test", profile(1)],
      [
        "get",
        /^\/omega-hub\/v1\/publishers\/c-test\/entries/,
        { count: 0, results: [] },
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubPublisherPage />, {
      path: "/dashboard/hub/publishers/:handle",
      at: "/dashboard/hub/publishers/c-test",
    });
    expect(await screen.findByTestId("hub-tier-badge")).toHaveTextContent(
      "Community reviewed",
    );
  });
});
