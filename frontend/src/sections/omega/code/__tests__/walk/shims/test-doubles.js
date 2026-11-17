// The test-only modules the pages' fixtures import, stood in for in a real
// browser (vite.config.mjs aliases them here): the fixtures' vi.fn becomes the
// function itself, and nothing renders through Testing Library.
export const vi = {
  fn: (impl = () => undefined) => impl,
  mock: () => undefined,
  hoisted: (make) => make(),
};

export function render() {
  throw new Error(
    "The walk harness renders with react-dom, not Testing Library.",
  );
}

/** The selected workspace (src/contexts/WorkspaceContext useWorkspace). */
export const useWorkspace = () => ({ currentWorkspaceId: "ws-a" });
