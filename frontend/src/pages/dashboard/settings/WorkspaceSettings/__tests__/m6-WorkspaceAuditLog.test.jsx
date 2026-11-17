// M6 (W4 §9 integration): Settings › Audit log reads the workspace scope of the
// one audit read, and the workspace settings nav offers it to admins only.
import { describe, expect, it, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";

const auditCalls = [];
// One stable answer, as react-query gives (the panel compares facets by
// identity).
const AUDIT = {
  data: {
    pages: [
      {
        data: {
          total: 1,
          groups: [],
          actors: [],
          entries: [
            {
              id: "e1",
              at: new Date().toISOString(),
              action: "dataset.access_changed",
              action_label: "changed access",
              actor: { id: "u1", name: "Maya Chen" },
              scope: { kind: "dataset", id: "d1", name: "GS" },
              target: { kind: "user", id: "u2", label: "Asha Rao" },
              summary: "Asha Rao: View → Edit",
              diff: [],
              changes: {},
            },
          ],
        },
      },
    ],
  },
  isLoading: false,
  hasNextPage: false,
};
vi.mock("src/api/develop/dataset-access", () => ({
  useAuditInfinite: (params, opts) => {
    auditCalls.push({ params, opts });
    return AUDIT;
  },
}));
let wsLevel = 8;
vi.mock("src/api/workspaces/list", () => ({
  useWorkspaceFromList: () => ({
    workspace: { id: "ws1", name: "Support Agents", user_ws_level: wsLevel },
  }),
  useWorkspacesList: () => ({ data: [] }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Member" } }),
}));

import WorkspaceAuditLog from "../WorkspaceAuditLog";
import { workspaceAuditScope } from "src/sections/common/Audit/auditModel";
import { useWorkspaceSettingsNav } from "src/layouts/dashboard/config-navigation";

beforeEach(() => {
  auditCalls.length = 0;
});

describe("Settings › Audit log", () => {
  it("reads scope=workspace:<id> and lists the entries", () => {
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/ws1/audit-log"]}>
          <Routes>
            <Route
              path="/:workspaceId/audit-log"
              element={<WorkspaceAuditLog />}
            />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );
    expect(workspaceAuditScope("ws1")).toBe("workspace:ws1");
    expect(auditCalls[0].params.scope).toBe("workspace:ws1");
    expect(auditCalls[0].opts.enabled).toBe(true);
    expect(screen.getByText("Audit log · Support Agents")).toBeTruthy();
    expect(screen.getByText(/Asha Rao: View → Edit/)).toBeTruthy();
    // A page, not a drawer: no close button.
    expect(screen.queryByLabelText("Close audit log")).toBeNull();
  });

  it("the workspace settings nav offers Audit log to workspace admins only", () => {
    wsLevel = 8;
    const admin = renderHook(() => useWorkspaceSettingsNav("ws1")).result
      .current;
    const adminItems = admin.data[0].items.map((i) => i.title);
    expect(adminItems).toContain("Audit log");
    expect(admin.data[0].items.find((i) => i.title === "Audit log").path).toBe(
      "/dashboard/settings/workspace/ws1/audit-log",
    );
    wsLevel = 3;
    const member = renderHook(() => useWorkspaceSettingsNav("ws1")).result
      .current;
    expect(member.data[0].items.map((i) => i.title)).not.toContain("Audit log");
  });
});
