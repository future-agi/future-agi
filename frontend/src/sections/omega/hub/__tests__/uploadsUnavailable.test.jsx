import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import meUser from "../__fixtures__/me-user.json";
import HubPublishPage from "../HubPublishPage";
import {
  UPLOADS_UNAVAILABLE,
  uploadsUnavailableCopy,
} from "../components/UploadsUnavailable";
import {
  UPLOADS_OFF_AUDIENCE,
  uploadsOffAudience,
} from "../lib/uploadsChecked";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
const auth = { role: "Owner" };
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
// The server's mode as useDeploymentMode() answers it. These pages run on a
// server confirmed self-hosted, where an owner or admin reads the setup.
const { SELF_HOSTED } = vi.hoisted(() => ({
  SELF_HOSTED: {
    mode: "oss",
    isCloud: false,
    isOSS: true,
    isEE: false,
    isLoading: false,
    isSuccess: true,
    isConfirmed: true,
  },
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => SELF_HOSTED,
}));
const CLOUD = { ...SELF_HOSTED, mode: "cloud", isCloud: true, isOSS: false };
const ENTERPRISE = { ...SELF_HOSTED, mode: "ee", isOSS: false, isEE: true };
// While the answer is on its way, or after it failed, "oss" stands in for it.
const LOADING = {
  ...SELF_HOSTED,
  isLoading: true,
  isSuccess: false,
  isConfirmed: false,
};
const FAILED = { ...SELF_HOSTED, isSuccess: false, isConfirmed: false };

// An upload on a server with no Hub checker configured is a configuration
// error, so the Publish page says what is missing instead of hiding. The
// server answers 503 OMEGA_HUB_CHECKER_NOT_CONFIGURED and stores nothing
// (omega_hub/services/intake.py). Everyone first reads one plain sentence, in
// a notice laid out with the rest of the work area, not a centred block. On a
// server confirmed self-hosted, an organization owner or admin then reads the
// setup steps under "For administrators", every fact of them, and a link to
// the self-host docs the app links to elsewhere; anyone else reads the
// sentence alone. On Cloud, and while the server's mode is not known,
// everyone reads only that uploads are off (uploadsOffReaders.test.jsx).

// The words a person with `role` reads on a server whose mode is
// `deployment`.
const wordsFor = (deployment, role) =>
  uploadsUnavailableCopy(uploadsOffAudience(deployment, role));

const LEAD =
  "Uploads are off on this server. Ask your administrator to start the Hub checker.";
const SELF_HOST_DOCS = "https://docs.futureagi.com/docs/self-hosting";
// Every fact of the setup, as an owner or admin reads it.
const FACTS = [
  "Until it runs, the server refuses every upload and stores nothing.",
  "Make sure OMEGA_HUB_CHECKER_TOKEN is set on the server.",
  "The Hub checker uses the same token.",
  "On Docker Compose, make sure hub is in COMPOSE_PROFILES in .env.",
  "The server does not read a --profile flag; it reads only that setting.",
  "Restart the server. Uploads work once it runs with both settings.",
];
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
const REFUSED = {
  error: {
    code: "OMEGA_HUB_CHECKER_NOT_CONFIGURED",
    message:
      "This Hub cannot check uploads: its sandboxed checker is not configured. Nothing was stored.",
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

// Every element from the sentence up to the panel that centres its text.
function centredAncestors(node, stop) {
  const centred = [];
  for (let at = node; at && at !== stop.parentElement; at = at.parentElement) {
    if (getComputedStyle(at).textAlign === "center") centred.push(at);
  }
  return centred;
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  auth.role = "Owner";
});
afterEach(() => vi.unstubAllEnvs());

describe("the words for a server with no Hub checker", () => {
  it("on a self-hosted server, lead with one plain sentence for everyone; only an organization owner or admin gets the setup steps", () => {
    for (const role of ["Owner", "Admin"]) {
      const copy = wordsFor(SELF_HOSTED, role);
      expect(copy).toBe(UPLOADS_UNAVAILABLE.admin);
      expect(copy.title).toBe(LEAD);
      expect(copy.heading).toBe("For administrators");
      expect(copy.steps).toHaveLength(3);
    }
    for (const role of ["Member", "Viewer", undefined]) {
      const copy = wordsFor(SELF_HOSTED, role);
      expect(copy.title).toBe(LEAD);
      expect(copy.heading).toBeNull();
      expect(copy.steps).toBeNull();
    }
    // Never a promise of a date or a "preview".
    const all = JSON.stringify(UPLOADS_UNAVAILABLE);
    expect(all).not.toMatch(/soon|preview|coming|\byet\b/iu);
  });

  it("on Cloud, and while the server's mode is not known, give everyone only that uploads are off", () => {
    for (const deployment of [CLOUD, LOADING, FAILED, undefined]) {
      for (const role of ["Owner", "Admin", "Member", undefined]) {
        const copy = wordsFor(deployment, role);
        expect(copy).toBe(UPLOADS_UNAVAILABLE.everyone);
        expect(copy.title).toBe("Uploads are off on this server.");
        expect(copy.heading).toBeNull();
        expect(copy.steps).toBeNull();
        expect(copy.docs).toBeNull();
      }
    }
  });

  it("are chosen in one place, from the server's confirmed mode and the person's role", () => {
    expect(UPLOADS_OFF_AUDIENCE).toEqual({
      admin: "admin",
      member: "member",
      everyone: "everyone",
    });
    const cases = [
      [SELF_HOSTED, "Owner", "admin"],
      [SELF_HOSTED, "Admin", "admin"],
      [SELF_HOSTED, "Member", "member"],
      [SELF_HOSTED, undefined, "member"],
      [ENTERPRISE, "Owner", "admin"],
      [ENTERPRISE, "Member", "member"],
      [CLOUD, "Owner", "everyone"],
      [CLOUD, "Admin", "everyone"],
      [CLOUD, "Member", "everyone"],
      [LOADING, "Owner", "everyone"],
      [FAILED, "Admin", "everyone"],
      [undefined, "Owner", "everyone"],
    ];
    for (const [deployment, role, audience] of cases)
      expect(uploadsOffAudience(deployment, role)).toBe(audience);
    // An audience the words do not know reads the short sentence.
    expect(uploadsUnavailableCopy("Owner")).toBe(UPLOADS_UNAVAILABLE.everyone);
  });
});

describe("the Publish page with no checker configured", () => {
  it("leads with the plain sentence in a notice, then gives an owner the setup steps under For administrators, in place of both ways to publish", async () => {
    serve([OMEGA_PART.HUB_CLI]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    expect(
      within(notice).getByRole("heading", {
        level: 2,
        name: "Upload a folder",
      }),
    ).toBeInTheDocument();
    const lead = within(notice).getByText(LEAD);
    expect(centredAncestors(lead, notice)).toEqual([]);
    const admins = within(notice).getByRole("heading", {
      level: 3,
      name: "For administrators",
    });
    expect(
      lead.compareDocumentPosition(admins) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const steps = within(notice).getByRole("list", {
      name: "For administrators",
    });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(3);
    for (const fact of FACTS) expect(notice).toHaveTextContent(fact);
    // The setting and the profile list are names to type: code, not prose.
    expect(within(notice).getByText("OMEGA_HUB_CHECKER_TOKEN").tagName).toBe(
      "CODE",
    );
    expect(within(notice).getByText("COMPOSE_PROFILES").tagName).toBe("CODE");
    expect(screen.queryByTestId("hub-upload-card")).toBeNull();
    expect(screen.queryByTestId("hub-publish-cli")).toBeNull();
    // The rest of the page (how publishing works, the rules) is still there.
    await screen.findByTestId("hub-rules");
  });

  it("tells a member the plain sentence, and nothing about server settings", async () => {
    auth.role = "Member";
    serve([]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    const lead = within(notice).getByText(LEAD);
    expect(centredAncestors(lead, notice)).toEqual([]);
    expect(
      within(notice).queryByRole("heading", { name: "For administrators" }),
    ).toBeNull();
    expect(document.body).not.toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
    expect(screen.queryByTestId("hub-upload-card")).toBeNull();
  });

  it("ends For administrators with a link to the self-host docs, in a new tab, under the setup steps", async () => {
    serve([]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    const admins = within(notice).getByRole("heading", {
      level: 3,
      name: "For administrators",
    });
    const steps = within(notice).getByRole("list", {
      name: "For administrators",
    });
    const docs = within(notice).getByRole("link", { name: "Self-host docs" });
    expect(docs).toHaveAttribute("href", SELF_HOST_DOCS);
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", "noopener noreferrer");
    expect(admins.closest("section")).toContainElement(docs);
    expect(
      steps.compareDocumentPosition(docs) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("gives a member no link to the server's docs", async () => {
    auth.role = "Member";
    serve([]);
    renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    expect(within(notice).getByText(LEAD)).toBeInTheDocument();
    expect(
      within(notice).queryByRole("link", { name: "Self-host docs" }),
    ).toBeNull();
  });

  it("never says uploads are unavailable while the server's answer is on its way", async () => {
    serve([]);
    const answer = axios.get.getMockImplementation();
    // The capabilities answer never arrives; every other call is answered.
    axios.get.mockImplementation((url, ...rest) =>
      String(url).includes("/api/capabilities/")
        ? new Promise(() => {})
        : answer(url, ...rest),
    );
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
    const file = new File(
      [new Uint8Array([0x1f, 0x8b, 0x08, 0x00])],
      "first-agent.tar.gz",
      { type: "application/gzip" },
    );
    fireEvent.change(screen.getByTestId("hub-upload-input"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByTestId("hub-upload-submit"));
    return screen.findByTestId("hub-upload-error");
  }

  it("gives an owner the plain sentence, then the setup steps (the capability answer said ready, the server knew better)", async () => {
    serve([OMEGA_PART.HUB_PUBLISH]);
    const alert = await uploadOnce();
    expect(alert).toHaveTextContent(LEAD);
    expect(alert).toHaveTextContent("For administrators:");
    expect(alert).toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
    expect(alert.textContent).not.toContain("`");
    await waitFor(() =>
      expect(calls.filter((call) => call.method === "post")).toHaveLength(1),
    );
  });

  it("gives a member only the plain sentence, never the server's setting", async () => {
    auth.role = "Member";
    serve([OMEGA_PART.HUB_PUBLISH]);
    const alert = await uploadOnce();
    expect(alert).toHaveTextContent(LEAD);
    expect(alert).not.toHaveTextContent("OMEGA_HUB_CHECKER_TOKEN");
  });
});
