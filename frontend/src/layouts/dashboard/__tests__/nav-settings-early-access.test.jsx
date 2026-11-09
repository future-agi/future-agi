import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";
import { rolloutQueryClient } from "src/sections/omega/shell/__tests__/rollout-fixtures";
import { ShellTestProviders } from "src/sections/omega/shell/__tests__/test-providers";

import { useNavSettingsData } from "../config-navigation";

// RC-01's Settings leg (READINESS 3.13, CP:D12, AUDIT R1). Settings → Early
// access is listed only while the server lists an Omega product this org is
// NOT admitted to: one taking waitlist requests (S2), or one whose request
// the org is waiting on. None while every product is internal (S0), and none
// for an org admitted to everything it can see (HIDE-UNFINISHED-FA round 2:
// tenants A and B, both admitted, saw a waitlist page with nothing on it).
// Loading, a failed read and the master switch off list nothing.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: true,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));

function SettingsTitles() {
  const sections = useNavSettingsData();
  const titles = sections.flatMap((section) =>
    section.items.map((item) => item.title),
  );
  return <div data-testid="settings-titles">{titles.join("|")}</div>;
}

function renderSettingsNav(queryClient) {
  render(
    <ShellTestProviders queryClient={queryClient}>
      {/* The settings nav reads the route for central billing (gateway UI v2, round 7). */}
      <MemoryRouter initialEntries={["/dashboard/settings/profile"]}>
        <SettingsTitles />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  return () => screen.getByTestId("settings-titles").textContent.split("|");
}

describe("Settings → Early access in the Settings nav", () => {
  beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("B at S0: admitted to nothing and nothing taking requests, so no entry", () => {
    const titles = renderSettingsNav(
      rolloutQueryClient({ admitted: [], requestable: [] }),
    );
    expect(titles()).toContain("Profile");
    expect(titles()).not.toContain("Early access");
  });

  it("B at S2: a product taking waitlist requests lists the entry", () => {
    const titles = renderSettingsNav(
      rolloutQueryClient({ admitted: [], requestable: ["hub"] }),
    );
    expect(titles()).toContain("Early access");
  });

  it("A, admitted to a product and nothing else listed, has no entry", () => {
    const titles = renderSettingsNav(rolloutQueryClient({ admitted: ["hub"] }));
    expect(titles()).toContain("Profile");
    expect(titles()).not.toContain("Early access");
  });

  it("admitted to every product, as tenants A and B are, has no entry", () => {
    const titles = renderSettingsNav(rolloutQueryClient());
    expect(titles()).toContain("Profile");
    expect(titles()).not.toContain("Early access");
  });

  it("admitted to some, with another taking requests, has the entry", () => {
    const titles = renderSettingsNav(
      rolloutQueryClient({ admitted: ["console"], requestable: ["hub"] }),
    );
    expect(titles()).toContain("Early access");
  });

  it("a product whose request the org is waiting on keeps the entry", async () => {
    vi.spyOn(axios, "get").mockResolvedValue({
      data: {
        can_request: true,
        products: [
          {
            product: "hub",
            label: "Omega Hub",
            admitted: false,
            state: null,
            request_status: "requested",
            requested_at: "2026-10-20T08:00:00Z",
          },
        ],
      },
    });
    const titles = renderSettingsNav(
      rolloutQueryClient({ earlyAccess: false }),
    );
    await waitFor(() => expect(titles()).toContain("Early access"));
  });

  it("lists nothing while the listing loads, and nothing when the read fails", async () => {
    let answer;
    const get = vi.spyOn(axios, "get").mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    const titles = renderSettingsNav(
      rolloutQueryClient({ earlyAccess: false }),
    );
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith("/omega-rollout/early-access/"),
    );
    expect(titles()).not.toContain("Early access");
    answer({ data: { can_request: true, products: [] } });
    await waitFor(() => expect(titles()).toContain("Profile"));
    expect(titles()).not.toContain("Early access");
  });

  it("lists nothing when the listing cannot be read", async () => {
    const get = vi
      .spyOn(axios, "get")
      .mockRejectedValue(new Error("503 verification_unavailable"));
    const titles = renderSettingsNav(
      rolloutQueryClient({ earlyAccess: false }),
    );
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(titles()).not.toContain("Early access");
  });

  it("with the master switch off it never asks the server and lists nothing", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    const get = vi.spyOn(axios, "get");
    const titles = renderSettingsNav(
      rolloutQueryClient({ earlyAccess: false }),
    );
    expect(titles()).not.toContain("Early access");
    expect(get).not.toHaveBeenCalledWith("/omega-rollout/early-access/");
  });
});
