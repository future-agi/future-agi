import { vi } from "vitest";

/**
 * Shared mocks for page tests: the selected workspace and the Omega session
 * probe (lane platform's useOmegaSession). Tests flip `session.state`.
 */
export const session = {
  state: "ready",
  code: null,
  message: null,
  recheck: vi.fn(),
};
export const workspace = { currentWorkspaceId: "ws-a" };
