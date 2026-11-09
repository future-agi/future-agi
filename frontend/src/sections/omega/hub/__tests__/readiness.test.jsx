import React from "react";
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import meUser from "../__fixtures__/me-user.json";
import HubNav from "../components/HubNav";
import HubPublishPage from "../HubPublishPage";
import HubSettingsPage from "../HubSettingsPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// HIDE-UNFINISHED-FA inside the Hub (seen only by the maintainer org until the
// Hub is ready): every Hub control that needs the Omega CLI waits for a CLI a
// customer can install (OMEGA_PART HUB_CLI), and no link points at a page that
// answers 404. Publish is never hidden: with no checker configured (HUB_PUBLISH
// false) its page says what is missing (main-session ruling 3, 2026-10-21;
// uploadsUnavailable.test.jsx).

const ME = {
  ...meUser,
  publishers: [{ handle: "c-test", displayName: "C Test", tier: "community", kind: "user" }],
};

function serve(parts) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", ME],
    ["get", "/omega-hub/v1/me/likes", []],
    ["get", "/omega-hub/v1/me/tokens", []],
    ["get", "/omega-hub/v1/submissions", []],
  ]);
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Publish page", () => {
  it("has no terminal block and no 'Recommended' while the CLI lacks `omega hub`", async () => {
    serve([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    await screen.findByTestId("hub-rules");
    await screen.findByTestId("hub-upload-card");
    expect(screen.queryByTestId("hub-publish-cli")).toBeNull();
    expect(screen.queryByText("Recommended")).toBeNull();
    expect(document.body).not.toHaveTextContent("omega hub login");
  });

  it("shows the terminal block once the CLI ships `omega hub`, with real code and no backticks", async () => {
    serve([OMEGA_PART.HUB_PUBLISH, OMEGA_PART.HUB_CLI]);
    renderAt(<HubPublishPage />);
    const block = await screen.findByTestId("hub-publish-cli");
    expect(block).toHaveTextContent("omega hub publish ./my-entry --wait");
    expect(block.querySelector("code")).toHaveTextContent("--wait");
    expect(block.textContent).not.toContain("`");
    expect(screen.queryByText("Recommended")).toBeNull();
  });

  it("links to no external rules page (docs.futureagi.com/hub answers 404)", async () => {
    serve([OMEGA_PART.HUB_PUBLISH, OMEGA_PART.HUB_CLI]);
    renderAt(<HubPublishPage />);
    await screen.findByTestId("hub-rules");
    const hrefs = [...document.querySelectorAll("a")].map((a) => a.getAttribute("href") || "");
    expect(hrefs.some((href) => href.includes("docs.futureagi.com"))).toBe(false);
    expect(screen.queryByText("Read the full rules")).toBeNull();
  });
});

describe("Hub nav and submissions", () => {
  it("offers Publish whether or not this server can check uploads", async () => {
    serve([]);
    const { unmount } = renderAt(<HubNav />);
    expect(await screen.findByTestId("hub-nav-publish")).toHaveAttribute("href", "/dashboard/hub/publish");
    unmount();
    serve([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubNav />);
    expect(await screen.findByTestId("hub-nav-publish")).toBeInTheDocument();
  });

  it("offers Publish in the empty submissions list even while uploads cannot be checked (the page says why)", async () => {
    serve([]);
    renderAt(<HubSubmissionsPage />);
    expect(await screen.findByText("Nothing submitted yet")).toBeInTheDocument();
    expect(within(screen.getByTestId("hub-empty")).getByRole("link", { name: "Publish" })).toHaveAttribute("href", "/dashboard/hub/publish");
  });
});

describe("Hub settings", () => {
  it("has no Hub tokens section while a token has nothing to log in to", async () => {
    serve([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubSettingsPage />);
    await screen.findByTestId("hub-my-likes");
    expect(screen.queryByTestId("hub-tokens")).toBeNull();
    expect(document.body).not.toHaveTextContent("Hub tokens");
  });

  it("shows Hub tokens once the CLI ships `omega hub`, naming the command as code", async () => {
    serve([OMEGA_PART.HUB_CLI]);
    renderAt(<HubSettingsPage />);
    const tokens = await screen.findByTestId("hub-tokens");
    expect(tokens.querySelector("code")).toHaveTextContent("omega hub login --token-stdin");
    expect(tokens.textContent).not.toContain("`");
  });
});
