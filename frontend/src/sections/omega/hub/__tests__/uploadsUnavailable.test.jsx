import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import meUser from "../__fixtures__/me-user.json";
import HubPublishPage from "../HubPublishPage";
import { UPLOADS_UNAVAILABLE, uploadsUnavailableCopy } from "../components/UploadsUnavailable";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
const auth = { role: "Owner" };
vi.mock("src/auth/hooks/use-auth-context", () => ({ useSafeAuthContext: () => auth }));

// Main-session ruling 3 (2026-10-21): an upload on a server with no Hub checker
// configured is a configuration error, so it says what is missing instead of
// hiding. The server answers 503 OMEGA_HUB_CHECKER_NOT_CONFIGURED and stores
// nothing (omega_hub/services/intake.py). An organization owner or admin reads
// the operator's message; anyone else reads "ask your administrator".

const MEMBER_WORDS = "Uploads are not available on this server yet: ask your administrator.";
const ME = {
  ...meUser,
  publishers: [{ handle: "c-test", displayName: "C Test", tier: "community", kind: "user" }],
};
const REFUSED = {
  error: {
    code: "OMEGA_HUB_CHECKER_NOT_CONFIGURED",
    message: "This Hub cannot check uploads: its sandboxed checker is not configured. Nothing was stored.",
    fix: "Ask the Hub's administrator to run the checker with OMEGA_HUB_CHECKER_TOKEN set, then upload again.",
  },
};

function serve(parts, capabilities = capabilitiesAnswer({ parts })) {
  respond([
    ["get", "/api/capabilities/", capabilities],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", ME],
    ["post", "/omega-hub/v1/submissions", REFUSED, 503],
  ]);
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  auth.role = "Owner";
});
afterEach(() => vi.unstubAllEnvs());

describe("the words for a server with no Hub checker", () => {
  it("names the setting to an organization owner or admin, and only asks anyone else to ask", () => {
    for (const role of ["Owner", "Admin"]) {
      const copy = uploadsUnavailableCopy(role);
      expect(copy).toBe(UPLOADS_UNAVAILABLE.admin);
      expect(`${copy.title} ${copy.detail}`).toContain("OMEGA_HUB_CHECKER_TOKEN");
    }
    for (const role of ["Member", "Viewer", undefined]) {
      expect(uploadsUnavailableCopy(role)).toEqual({ title: MEMBER_WORDS, detail: null });
    }
    // Never a promise of a date or a "preview".
    const all = JSON.stringify(UPLOADS_UNAVAILABLE);
    expect(all).not.toMatch(/soon|preview|coming/iu);
  });
});

describe("the Publish page with no checker configured", () => {
  it("stays a page and tells an owner what is missing, in place of both ways to publish", async () => {
    serve([OMEGA_PART.HUB_CLI]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    expect(notice).toHaveTextContent(UPLOADS_UNAVAILABLE.admin.title);
    expect(notice).toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
    expect(screen.queryByTestId("hub-upload-card")).toBeNull();
    expect(screen.queryByTestId("hub-publish-cli")).toBeNull();
    // The rest of the page (how publishing works, the rules) is still there.
    await screen.findByTestId("hub-rules");
  });

  it("tells a member to ask their administrator, and nothing about server settings", async () => {
    auth.role = "Member";
    serve([]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    expect(notice).toHaveTextContent(MEMBER_WORDS);
    expect(document.body).not.toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
    expect(screen.queryByTestId("hub-upload-card")).toBeNull();
  });

  it("never says uploads are unavailable while the server's answer is on its way", async () => {
    serve([]);
    const answer = axios.get.getMockImplementation();
    // The capabilities answer never arrives; every other call is answered.
    axios.get.mockImplementation((url, ...rest) => (String(url).includes("/api/capabilities/") ? new Promise(() => {}) : answer(url, ...rest)));
    try {
      renderAt(<HubPublishPage />);
      await screen.findByTestId("hub-rules");
      expect(screen.queryByTestId("hub-uploads-unavailable")).toBeNull();
      expect(screen.queryByTestId("hub-upload-card")).toBeNull();
    } finally {
      axios.get.mockImplementation(answer);
    }
  });

  it("shows the upload card when the server can check uploads", async () => {
    serve([OMEGA_PART.HUB_PUBLISH]);
    renderAt(<HubPublishPage />);
    expect(await screen.findByTestId("hub-upload-card")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-uploads-unavailable")).toBeNull();
  });
});

describe("an upload the server refuses because no checker is configured", () => {
  async function uploadOnce() {
    renderAt(<HubPublishPage />);
    await screen.findByTestId("hub-upload-card");
    const file = new File([new Uint8Array([0x1f, 0x8b, 0x08, 0x00])], "first-agent.tar.gz", { type: "application/gzip" });
    fireEvent.change(screen.getByTestId("hub-upload-input"), { target: { files: [file] } });
    fireEvent.click(screen.getByTestId("hub-upload-submit"));
    return screen.findByTestId("hub-upload-error");
  }

  it("gives an owner the operator's message (the capability answer said ready, the server knew better)", async () => {
    serve([OMEGA_PART.HUB_PUBLISH]);
    const alert = await uploadOnce();
    expect(alert).toHaveTextContent(UPLOADS_UNAVAILABLE.admin.title);
    expect(alert).toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
    await waitFor(() => expect(calls.filter((call) => call.method === "post")).toHaveLength(1));
  });

  it("gives a member only 'ask your administrator', never the server's setting", async () => {
    auth.role = "Member";
    serve([OMEGA_PART.HUB_PUBLISH]);
    const alert = await uploadOnce();
    expect(alert).toHaveTextContent(MEMBER_WORDS);
    expect(alert).not.toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
  });
});
