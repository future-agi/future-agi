import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useRequestColumns from "../useRequestColumns";
import { DEFAULT_CONFIG } from "../columnModel";

const authState = { user: { id: "user-a" } };
const orgState = { currentOrganizationId: "org-1", isReady: true };
const declarationsState = {
  data: [{ name: "tenant", organization: "org-1" }],
  status: "success",
  isFetching: false,
  isPlaceholderData: false,
  refetch: vi.fn(),
};

vi.mock("src/auth/hooks/use-auth-context", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => orgState,
}));

vi.mock("../../../custom-properties/hooks/useCustomProperties", () => ({
  useCustomProperties: () => declarationsState,
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const KEY = "agentcc.requestLogs.columns.v1:user-a:org-1:requests";

function createMemoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: vi.fn((k) => (map.has(k) ? map.get(k) : null)),
    setItem: vi.fn((k, v) => map.set(k, String(v))),
    removeItem: vi.fn((k) => map.delete(k)),
    clear: vi.fn(() => map.clear()),
    key: vi.fn((i) => Array.from(map.keys())[i] ?? null),
    get length() {
      return map.size;
    },
  };
}

const labels = (result) => result.current.columns.map((c) => c.label);

describe("useRequestColumns with the Agent & billing group", () => {
  let storage;

  beforeEach(() => {
    storage = createMemoryStorage();
    vi.stubGlobal("localStorage", storage);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("offers the eight columns unchecked and counts them in the totals", () => {
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result)).toHaveLength(10);
    expect(result.current.pickerEntries.gateway).toHaveLength(8);
    expect(result.current.pickerEntries.gateway.every((e) => !e.checked)).toBe(
      true,
    );
    // Ten built-ins, one declared custom property, eight gateway columns.
    expect(result.current.visibleCount).toBe(10);
    expect(result.current.totalCount).toBe(19);
  });

  it("toggles a gateway column on into the table, persists it, and Reset removes it", () => {
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("gateway:agentRunId"));
    expect(labels(result).at(-1)).toBe("Run");
    expect(result.current.columns.at(-1).kind).toBe("gateway");
    expect(result.current.visibleCount).toBe(11);
    expect(
      JSON.parse(storage.setItem.mock.calls.at(-1)[1]).columns.at(-1),
    ).toEqual({ id: "gateway:agentRunId" });

    act(() => result.current.reset());
    expect(labels(result)).toHaveLength(10);
    expect(result.current.pickerEntries.gateway.every((e) => !e.checked)).toBe(
      true,
    );
  });

  it("renders a saved gateway column where the config puts it", () => {
    storage = createMemoryStorage({
      [KEY]: JSON.stringify({
        v: 1,
        columns: [
          DEFAULT_CONFIG.columns[0],
          { id: "gateway:billingMode" },
          ...DEFAULT_CONFIG.columns.slice(1),
          { id: "gateway:agentStep" },
        ],
        hidden: [],
      }),
    });
    vi.stubGlobal("localStorage", storage);
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result).slice(0, 3)).toEqual([
      "Timestamp",
      "Billing mode",
      "Model",
    ]);
    expect(labels(result).at(-1)).toBe("Step");
  });

  it("moves gateway columns among themselves", () => {
    storage = createMemoryStorage({
      [KEY]: JSON.stringify({
        v: 1,
        columns: [
          ...DEFAULT_CONFIG.columns,
          { id: "gateway:billingMode" },
          { id: "gateway:agentStep" },
        ],
        hidden: [],
      }),
    });
    vi.stubGlobal("localStorage", storage);
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.move("gateway:agentStep", -1));
    expect(labels(result).slice(-2)).toEqual(["Step", "Billing mode"]);
    expect(
      result.current.pickerEntries.gateway.slice(0, 2).map((e) => e.label),
    ).toEqual(["Step", "Billing mode"]);
  });
});
