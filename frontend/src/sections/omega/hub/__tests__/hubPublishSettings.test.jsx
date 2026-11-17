import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import rules from "../__fixtures__/rules.json";
import HubPublishPage from "../HubPublishPage";
import HubSettingsPage from "../HubSettingsPage";
import TokenSettings from "../components/TokenSettings";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

// Publish reads as steps in order (what happens after you submit, then each
// command), the rules fold behind one control, and Submit says why it is off.
// Hub settings shows the publisher a person already has before any form,
// keeps "Create your own publisher" behind a button, lists Hub tokens as a
// table with the plain sign-in command in a copy box, and puts a refused
// handle on its field with the server's fix. When the registry gives no
// reason (a gateway error or a dropped connection), each message says what
// failed instead of "Request failed (502)" or "Network Error", and a list
// that loaded stays in view when a later refresh fails. A self-hosted
// registry proves every command carries it.

const config = vi.hoisted(() => ({
  apiBase: "https://registry.example.test/api/",
}));
vi.mock("src/config-global", () => ({
  get HOST_API() {
    return config.apiBase;
  },
}));
vi.mock("src/utils/axios", () => import("./mockAxios"));

const REGISTRY = "https://registry.example.test/api/omega-hub/v1";
const TOKENS = "/omega-hub/v1/me/tokens";
const NO_ANSWER = "The Hub registry did not answer; try again in a moment.";
const DAY = 24 * 60 * 60 * 1000;
const ago = (days) => new Date(Date.now() - days * DAY).toISOString();
const ahead = (days) => new Date(Date.now() + days * DAY).toISOString();
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const exactly = (text) => new RegExp(`^${escapeRe(text)}$`);

const ME = {
  ...meUser,
  publishers: [
    {
      handle: "c-test",
      displayName: "C Test",
      tier: "community",
      kind: "user",
    },
  ],
};
const TAKEN = {
  error: {
    code: "OMEGA_HUB_HANDLE_TAKEN",
    message: "This handle is taken or was used before.",
    fix: "Choose another handle.",
  },
};
const UNVERIFIED = {
  error: {
    code: "OMEGA_HUB_EMAIL_UNVERIFIED",
    message: "Confirm your email address first.",
    fix: "",
  },
};
const TOKEN_ROWS = [
  {
    id: "token-laptop",
    name: "laptop",
    prefix: "oh_ab12c",
    publisher: "future-agi",
    scopes: ["publish"],
    createdAt: ago(3),
    lastUsedAt: null,
    expiresAt: ahead(87),
    revokedAt: null,
  },
  {
    id: "token-ci",
    name: "ci runner",
    prefix: "oh_cd34e",
    publisher: "future-agi",
    scopes: ["publish"],
    createdAt: ago(10),
    lastUsedAt: ago(1),
    expiresAt: ahead(20),
    revokedAt: null,
  },
];

function servePublish(parts, me = ME) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", me],
  ]);
}

function serveSettings({
  me = meMaintainer,
  parts = [OMEGA_PART.HUB_CLI],
  tokens = [],
  extra = [],
} = {}) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", me],
    ["get", "/omega-hub/v1/me/likes", []],
    ["get", TOKENS, tokens],
    ...extra,
  ]);
}

const headings = (level) =>
  screen.getAllByRole("heading", { level }).map((node) => node.textContent);

const writeText = vi.fn(() => Promise.resolve());

beforeEach(() => {
  config.apiBase = "https://registry.example.test/api/";
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  writeText.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});
afterEach(() => vi.unstubAllEnvs());

describe("Publish reads in the order things happen", () => {
  it("gives the page one heading per part, in reading order", async () => {
    servePublish([OMEGA_PART.HUB_CLI, OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    await screen.findByTestId("hub-publish-cli");
    await screen.findByTestId("hub-rules");
    expect(
      screen.getByRole("heading", { level: 1, name: "Publish to the Hub" }),
    ).toBeInTheDocument();
    expect(headings(2)).toEqual([
      "What happens after you submit",
      "Publish from your terminal",
      "Upload a folder",
      "Check two fields before every upload",
      "Hub rules",
    ]);
  });

  it("says what happens after you submit, with the review time from the rules", async () => {
    servePublish([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    const pipeline = await screen.findByRole("list", {
      name: "What happens after you submit",
    });
    const steps = within(pipeline).getAllByRole("listitem");
    expect(steps).toHaveLength(4);
    expect(steps[0]).toHaveTextContent("1. Received");
    expect(steps[1]).toHaveTextContent("2. Automated checks");
    expect(steps[2]).toHaveTextContent("3. Maintainer review");
    expect(steps[3]).toHaveTextContent("4. Published");
    await waitFor(() =>
      expect(steps[2]).toHaveTextContent(rules.targets.review),
    );
    // No timing is promised for the checks; nothing measures it.
    expect(steps[1]).not.toHaveTextContent(/minute|second/i);
  });

  it("numbers the terminal steps and gives each command whole, in this registry's form", async () => {
    servePublish([OMEGA_PART.HUB_CLI, OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    const block = await screen.findByTestId("hub-publish-cli");
    const steps = within(within(block).getByRole("list")).getAllByRole(
      "listitem",
    );
    expect(
      steps.map(
        (step) => within(step).getByRole("heading", { level: 3 }).textContent,
      ),
    ).toEqual([
      "Get a Hub token",
      "Sign in on this machine",
      "Publish the folder",
      "Follow it later",
    ]);
    expect(
      within(steps[0]).getByRole("link", { name: "Create a Hub token" }),
    ).toHaveAttribute("href", "/dashboard/hub/settings");
    expect(steps[1]).toHaveTextContent(
      `omega hub login --registry ${REGISTRY}`,
    );
    expect(steps[1]).toHaveTextContent(
      "In CI, add --token-stdin to this command and pipe the token in.",
    );
    expect(steps[2]).toHaveTextContent(
      `omega hub publish ./my-entry --wait --registry ${REGISTRY}`,
    );
    expect(steps[3]).toHaveTextContent(
      `omega hub status --registry ${REGISTRY}`,
    );
    expect(block.textContent).not.toContain("`");
    expect(screen.queryByText("Recommended")).toBeNull();
  });

  it("names the manifest file as code in the two fields to check", async () => {
    servePublish([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    const manifest = await screen.findByTestId("hub-publish-manifest");
    expect(within(manifest).getByText("omega.hub.json").tagName).toBe("CODE");
    expect(manifest).toHaveTextContent(
      "give each upload a version you have not published and a disclosure",
    );
  });

  it("folds the Hub rules behind one control, and opens them in place", async () => {
    servePublish([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    const box = await screen.findByTestId("hub-rules");
    const toggle = within(box).getByRole("button", { name: "Hub rules" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(box).toHaveTextContent(
      `Rules version ${rules.version}, in effect since 14 Oct 2026`,
    );
    const first = within(box).getByText(rules.content[0].text);
    expect(first).not.toBeVisible();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(first).toBeVisible();
    expect(
      document.getElementById(toggle.getAttribute("aria-controls")),
    ).toContainElement(first);
    fireEvent.click(toggle);
    expect(first).not.toBeVisible();
  });

  it("says why Submit for review is off, next to it and in its description", async () => {
    servePublish([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    const card = await screen.findByTestId("hub-upload-card");
    expect(
      within(card).getByRole("heading", { level: 2, name: "Upload a folder" }),
    ).toBeInTheDocument();
    const submit = within(card).getByRole("button", {
      name: "Submit for review",
    });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAccessibleDescription("Choose a file first.");
    expect(within(card).getByText("Choose a file first.")).toBeVisible();
    const file = new File([new Uint8Array([0x1f, 0x8b])], "my-entry.tar.gz", {
      type: "application/gzip",
    });
    fireEvent.change(screen.getByTestId("hub-upload-input"), {
      target: { files: [file] },
    });
    expect(submit).toBeEnabled();
    expect(within(card).queryByText("Choose a file first.")).toBeNull();
    expect(screen.getByTestId("hub-drop-zone")).toHaveTextContent(
      "my-entry.tar.gz",
    );
  });
});

describe("Hub settings shows what you have before any form", () => {
  it("gives each section a heading, in order", async () => {
    serveSettings();
    renderAt(<HubSettingsPage />);
    await screen.findByTestId("hub-tokens");
    expect(headings(2)).toEqual([
      "Your publisher",
      "Hub tokens",
      "Entries you liked",
    ]);
  });

  it("shows the profile as text, with Edit profile opening the form and Cancel returning focus", async () => {
    serveSettings({ parts: [] });
    renderAt(<HubSettingsPage />);
    const profile = await screen.findByTestId("hub-publisher-profile");
    expect(
      within(profile).getByRole("heading", { level: 3 }),
    ).toHaveTextContent("Future AGI");
    expect(profile).toHaveTextContent("@future-agi");
    expect(profile).toHaveTextContent(
      "Official Omega Hub entries, written and reviewed by Future AGI.",
    );
    const edit = within(profile).getByRole("button", { name: "Edit profile" });
    expect(edit).toHaveAttribute("aria-expanded", "false");
    const name = within(profile).getByLabelText("Display name");
    expect(name).not.toBeVisible();
    fireEvent.click(edit);
    expect(edit).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(name).toHaveFocus());
    expect(name).toHaveValue("Future AGI");
    expect(within(profile).getByLabelText("Bio")).toHaveValue(
      "Official Omega Hub entries, written and reviewed by Future AGI.",
    );
    fireEvent.click(within(profile).getByRole("button", { name: "Cancel" }));
    expect(edit).toHaveFocus();
    expect(name).not.toBeVisible();
    expect(calls.filter((call) => call.method === "patch")).toHaveLength(0);
  });

  it("keeps Create your own publisher behind a button when you already act for one", async () => {
    serveSettings({ parts: [] });
    renderAt(<HubSettingsPage />);
    const profile = await screen.findByTestId("hub-publisher-profile");
    expect(screen.queryByTestId("hub-publisher-handle")).toBeNull();
    const open = screen.getByRole("button", {
      name: "Create your own publisher",
    });
    expect(open).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(open);
    expect(open).toHaveAttribute("aria-expanded", "true");
    const form = screen.getByTestId("hub-create-publisher");
    expect(
      profile.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      within(form).getByRole("heading", { level: 3, name: "New publisher" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("hub-publisher-handle")).toHaveFocus(),
    );
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("hub-publisher-handle")).toBeNull();
    expect(open).toHaveFocus();
  });

  it("opens the create form at once for a person with no publisher", async () => {
    serveSettings({ me: meUser, parts: [] });
    renderAt(<HubSettingsPage />);
    expect(
      await screen.findByTestId("hub-publisher-handle"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create your own publisher" }),
    ).toBeNull();
  });

  it("titles the section Create your publisher for a person with no publisher, with no second heading over the form", async () => {
    serveSettings({ me: meUser, parts: [] });
    renderAt(<HubSettingsPage />);
    await screen.findByTestId("hub-publisher-handle");
    expect(headings(2)[0]).toBe("Create your publisher");
    expect(
      screen.queryByRole("heading", { level: 3, name: "New publisher" }),
    ).toBeNull();
    expect(screen.getByRole("form", { name: "New publisher" })).toBe(
      screen.getByTestId("hub-create-publisher"),
    );
  });

  it("puts a refused handle on the Handle field, with the server's fix", async () => {
    serveSettings({
      parts: [],
      extra: [["post", "/omega-hub/v1/publishers", TAKEN, 409]],
    });
    renderAt(<HubSettingsPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Create your own publisher" }),
    );
    fireEvent.change(screen.getByTestId("hub-publisher-handle"), {
      target: { value: "future" },
    });
    fireEvent.change(screen.getByTestId("hub-publisher-display-name"), {
      target: { value: "Future" },
    });
    fireEvent.click(screen.getByTestId("hub-publisher-create"));
    const handle = screen.getByTestId("hub-publisher-handle");
    await waitFor(() => expect(handle).toHaveAttribute("aria-invalid", "true"));
    expect(handle).toHaveAccessibleDescription(
      "This handle is taken or was used before. Choose another handle.",
    );
    expect(calls.filter((call) => call.method === "post")).toHaveLength(1);
  });

  it("says the confirmation link was not sent when the registry gives no reason", async () => {
    serveSettings({
      me: meUser,
      parts: [],
      extra: [
        ["post", "/omega-hub/v1/publishers", UNVERIFIED, 403],
        ["post", "/omega-hub/v1/me/email-verification", {}, 502],
      ],
    });
    renderAt(<HubSettingsPage />);
    const handle = await screen.findByTestId("hub-publisher-handle");
    fireEvent.change(handle, { target: { value: "c-test" } });
    fireEvent.change(screen.getByTestId("hub-publisher-display-name"), {
      target: { value: "C Test" },
    });
    fireEvent.click(screen.getByTestId("hub-publisher-create"));
    fireEvent.click(await screen.findByTestId("hub-email-verify-send"));
    const error = await screen.findByTestId("hub-email-verify-error");
    expect(error).toHaveTextContent(`The link was not sent. ${NO_ANSWER}`);
    expect(error).not.toHaveTextContent(/Request failed/);
  });

  it("says the profile was not saved when the connection drops, never the bare error", async () => {
    serveSettings({ parts: [] });
    renderAt(<HubSettingsPage />);
    const profile = await screen.findByTestId("hub-publisher-profile");
    fireEvent.click(
      within(profile).getByRole("button", { name: "Edit profile" }),
    );
    axios.patch.mockImplementationOnce(() =>
      Promise.reject({ message: "Network Error" }),
    );
    fireEvent.click(
      within(profile).getByRole("button", { name: "Save profile" }),
    );
    const alert = await within(profile).findByRole("alert");
    expect(alert).toHaveTextContent(`The profile was not saved. ${NO_ANSWER}`);
    expect(alert).not.toHaveTextContent("Network Error");
  });

  it("says a timed-out profile save was not saved, never the transport text", async () => {
    serveSettings({ parts: [] });
    renderAt(<HubSettingsPage />);
    const profile = await screen.findByTestId("hub-publisher-profile");
    fireEvent.click(
      within(profile).getByRole("button", { name: "Edit profile" }),
    );
    axios.patch.mockImplementationOnce(() =>
      Promise.reject({ message: "timeout of 30000ms exceeded" }),
    );
    fireEvent.click(
      within(profile).getByRole("button", { name: "Save profile" }),
    );
    const alert = await within(profile).findByRole("alert");
    expect(alert).toHaveTextContent(`The profile was not saved. ${NO_ANSWER}`);
    expect(alert).not.toHaveTextContent(/timeout/);
  });

  it("says the entries you liked did not load, never a bare status", async () => {
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
      ["get", "/omega-hub/v1/me", meMaintainer],
      ["get", "/omega-hub/v1/me/likes", {}, 502],
    ]);
    renderAt(<HubSettingsPage />);
    const likes = await screen.findByTestId("hub-my-likes");
    // A 5xx is asked again once before it shows.
    await waitFor(
      () =>
        expect(likes).toHaveTextContent(
          `The entries you liked did not load. ${NO_ANSWER}`,
        ),
      { timeout: 4000 },
    );
    expect(likes).not.toHaveTextContent(/Request failed/);
    expect(
      within(likes).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("offers a way to the Hub when you have liked nothing", async () => {
    serveSettings({ parts: [] });
    renderAt(<HubSettingsPage />);
    const likes = await screen.findByTestId("hub-my-likes");
    expect(
      await within(likes).findByRole("link", { name: "Browse the Hub" }),
    ).toHaveAttribute("href", "/dashboard/hub");
  });
});

describe("Hub tokens", () => {
  it("gives the plain sign-in command in a copy box, and the CI form as one line", async () => {
    respond([["get", TOKENS, []]]);
    renderAt(<TokenSettings publishers={[]} />);
    const section = screen.getByTestId("hub-tokens");
    expect(
      within(section).getByRole("heading", { level: 2, name: "Hub tokens" }),
    ).toBeInTheDocument();
    expect(section.querySelector("code")).toHaveTextContent(
      exactly(`omega hub login --registry ${REGISTRY}`),
    );
    expect(section).toHaveTextContent(
      "In CI, add --token-stdin to this command and pipe the token in.",
    );
    expect(section).not.toHaveTextContent("Paste it into");
    expect(
      await within(section).findByText(/^You have no Hub tokens\./),
    ).toBeInTheDocument();
  });

  it("lists each token with its prefix to copy, when it was created and last used, and when it expires", async () => {
    respond([["get", TOKENS, TOKEN_ROWS]]);
    renderAt(<TokenSettings publishers={[]} />);
    const laptop = (await screen.findByText("laptop")).closest("li");
    expect(laptop).toHaveTextContent("oh_ab12c");
    expect(laptop).toHaveTextContent("@future-agi");
    expect(within(laptop).getByText("3 days ago")).toBeInTheDocument();
    expect(within(laptop).getByText("Never used")).toBeInTheDocument();
    expect(within(laptop).getByText("in 87 days")).toBeInTheDocument();
    expect(within(laptop).getAllByText(/ UTC$/)).toHaveLength(2);
    const ci = screen.getByText("ci runner").closest("li");
    expect(within(ci).getByText("24 h ago")).toBeInTheDocument();
    fireEvent.click(
      within(laptop).getByRole("button", {
        name: "Copy the prefix of laptop",
      }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("oh_ab12c"));
  });

  it("names the token, its prefix and its publisher before revoking it", async () => {
    respond([["get", TOKENS, TOKEN_ROWS]]);
    renderAt(<TokenSettings publishers={[]} />);
    const laptop = (await screen.findByText("laptop")).closest("li");
    const revoke = within(laptop).getByRole("button", { name: "Revoke" });
    expect(revoke).toHaveAccessibleDescription("laptop");
    fireEvent.click(revoke);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Revoke laptop?");
    expect(dialog).toHaveTextContent("oh_ab12c");
    expect(dialog).toHaveTextContent("@future-agi");
    expect(calls.filter((call) => call.method === "delete")).toHaveLength(0);
  });

  it("keeps the token list in view when a later refresh fails", async () => {
    respond([["get", TOKENS, TOKEN_ROWS]]);
    renderAt(<TokenSettings publishers={[]} />);
    const laptop = (await screen.findByText("laptop")).closest("li");
    respond([
      ["get", TOKENS, {}, 503],
      ["delete", `${TOKENS}/token-laptop`, {}],
    ]);
    fireEvent.click(within(laptop).getByRole("button", { name: "Revoke" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Revoke token",
      }),
    );
    const section = screen.getByTestId("hub-tokens");
    // The refresh after the revoke answers 503 twice (one retry) before it
    // shows.
    await waitFor(
      () =>
        expect(section).toHaveTextContent(
          `The token list did not refresh and may be out of date. ${NO_ANSWER}`,
        ),
      { timeout: 4000 },
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(within(section).getByRole("list")).toBeInTheDocument();
    expect(within(section).getByText("ci runner")).toBeInTheDocument();
    expect(section).not.toHaveTextContent("Your tokens did not load.");
    expect(
      within(section).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("chooses a publisher made after the page opened, so Create token works at once", async () => {
    respond([["get", TOKENS, []]]);
    const { rerender } = renderAt(<TokenSettings publishers={[]} />);
    expect(screen.queryByRole("button", { name: "Create token" })).toBeNull();
    rerender(<TokenSettings publishers={[{ handle: "c-test" }]} />);
    expect(screen.getByRole("button", { name: "Create token" })).toBeEnabled();
  });

  it("says the token was not created when the registry gives no reason", async () => {
    respond([
      ["get", TOKENS, []],
      ["post", TOKENS, {}, 502],
    ]);
    renderAt(<TokenSettings publishers={[{ handle: "future-agi" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(`The token was not created. ${NO_ANSWER}`);
    expect(alert).not.toHaveTextContent(/Request failed/);
    expect(calls.filter((call) => call.method === "post")).toHaveLength(1);
  });
});
