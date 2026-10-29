import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNavData } from "../config-navigation";

const authState = { user: null };

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));

function simulateTitles() {
  const { result } = renderHook(() => useNavData(), { wrapper: MemoryRouter });
  const section = result.current.find((s) => s.subheader === "Simulate");
  return section.items.map((item) => item.title);
}

describe("Simulate menu", () => {
  beforeEach(() => {
    authState.user = { organization_role: "Owner" };
  });

  it("shows only Environment to an org restricted to Environments", () => {
    authState.user.restrict_simulate_to_environments = true;
    expect(simulateTitles()).toEqual(["Environment"]);
  });

  it.each([false, undefined])(
    "shows every Simulate page when the flag is %s",
    (flag) => {
      authState.user.restrict_simulate_to_environments = flag;
      expect(simulateTitles()).toEqual([
        "Environment",
        "Scenarios",
        "Run Simulation",
      ]);
    },
  );
});
