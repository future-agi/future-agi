// @vitest-environment jsdom
// Organize verbs follow their independent capabilities, not a bundled host.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, act } from "@testing-library/react";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

vi.mock("src/utils/axios", () => ({
  default: { put: vi.fn(), get: vi.fn() },
  endpoints: {
    develop: { updateDataset: (id) => `/edit/${id}/` },
    agentPlayground: { listGraphs: "/graphs/" },
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: vi.fn(() => ({ undone: Promise.resolve(false) })),
  default: vi.fn(),
}));

const { default: useDatasetOrganize } = await import("../useDatasetOrganize");
const { useCommandStore, registeredCommands } = await import(
  "src/sections/common/CommandPalette/commandRegistry"
);
const { setRealBackendForTests } = await import(
  "src/sections/develop/realBackendCaps"
);

const DS = {
  id: "d1",
  name: "Golden",
  metadata: {},
  folder: "",
  agent_ids: [],
};

const organizeVerbs = () =>
  registeredCommands(useCommandStore.getState().sources).filter((c) =>
    String(c.id).startsWith("datasets.organize."),
  );

afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
  setLaunchOverrideForTest(null);
});

describe("L1 organise verbs follow the list's organise capability", () => {
  it("the real set registers none of them, with a row active or selected", () => {
    setRealBackendForTests(true);
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    const { result } = renderHook(() =>
      useDatasetOrganize({ selected: [DS], active: DS, canEdit: true }),
    );
    expect(useCommandStore.getState().sources["datasets.organize"]).toEqual([]);
    expect(organizeVerbs()).toEqual([]);
    // No door opens a dialog either (nothing would render it).
    act(() => result.current.openFor("folder"));
    expect(result.current.dialogs).toBeNull();
  });

  it("folder hold blocks folder, metadata and agent commands", () => {
    setRealBackendForTests(true);
    setLaunchOverrideForTest({
      launch: true,
      madeReal: ["list.organize.folders"],
    });
    const { result } = renderHook(() =>
      useDatasetOrganize({ selected: [DS], canEdit: true }),
    );
    expect(organizeVerbs().map((command) => command.label)).toEqual([]);
    act(() => result.current.openFor("metadata"));
    expect(result.current.dialogs).toBeNull();
    act(() => result.current.openFor("agents"));
    expect(result.current.dialogs).toBeNull();
    act(() => result.current.openFor("folder"));
    expect(result.current.dialogs).toBeNull();
  });

  it("the prototype set still offers all three on the active row", () => {
    setRealBackendForTests(false);
    renderHook(() =>
      useDatasetOrganize({ selected: [], active: DS, canEdit: true }),
    );
    expect(organizeVerbs().map((c) => c.label)).toEqual([
      "Set metadata…",
      "Move to folder…",
      "Attach to agents…",
    ]);
  });
});
