import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useRequestColumns from "../useRequestColumns";
import { DEFAULT_CONFIG } from "../columnModel";

const authState = { user: { id: "user-a" } };
const orgState = { currentOrganizationId: "org-1", isReady: true };
const declarationsState = {
  data: [{ name: "tenant", organization: "org-1" }],
  status: "success",
  isError: false,
  isPending: false,
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

const KEY_A = "agentcc.requestLogs.columns.v1:user-a:org-1:requests";
const KEY_B = "agentcc.requestLogs.columns.v1:user-a:org-2:requests";

function createMemoryStorage() {
  const map = new Map();
  return {
    getItem: vi.fn((k) => (map.has(k) ? map.get(k) : null)),
    setItem: vi.fn((k, v) => {
      map.set(k, String(v));
    }),
    removeItem: vi.fn((k) => {
      map.delete(k);
    }),
    clear: vi.fn(() => map.clear()),
    key: vi.fn((i) => Array.from(map.keys())[i] ?? null),
    get length() {
      return map.size;
    },
  };
}

const labels = (result) => result.current.columns.map((c) => c.label);

describe("useRequestColumns", () => {
  let storage;

  beforeEach(() => {
    storage = createMemoryStorage();
    vi.stubGlobal("localStorage", storage);
    authState.user = { id: "user-a" };
    orgState.currentOrganizationId = "org-1";
    orgState.isReady = true;
    declarationsState.data = [{ name: "tenant", organization: "org-1" }];
    declarationsState.status = "success";
    declarationsState.isError = false;
    declarationsState.isPending = false;
    declarationsState.refetch = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the ten default columns with no stored config (AC1)", () => {
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result)).toEqual([
      "Timestamp",
      "Model",
      "Provider",
      "Application",
      "Service",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
      "Session ID",
    ]);
    expect(result.current.storageKey).toBe(KEY_A);
    expect(result.current.storageNotice).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("toggle, move and reset update columns and persist ids only (AC2/AC3/AC13)", () => {
    const { result } = renderHook(() => useRequestColumns());

    act(() => result.current.toggle("metadata:tenant"));
    expect(labels(result).slice(-1)).toEqual(["tenant"]);
    const saved = JSON.parse(storage.getItem(KEY_A));
    expect(saved.columns.slice(-1)).toEqual([{ id: "metadata:tenant" }]);
    expect(JSON.stringify(saved)).not.toContain("acme");

    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");

    // Hidden Provider keeps its slot, so one step passes it and a second
    // step passes Application (J2).
    act(() => result.current.move("builtin:model", 1));
    expect(result.current.config.columns.slice(0, 3).map((c) => c.id)).toEqual([
      "builtin:startedAt",
      "builtin:provider",
      "builtin:model",
    ]);
    expect(labels(result).slice(0, 3)).toEqual([
      "Timestamp",
      "Model",
      "Application",
    ]);
    act(() => result.current.move("builtin:model", 1));
    expect(labels(result).slice(0, 3)).toEqual([
      "Timestamp",
      "Application",
      "Model",
    ]);

    act(() => result.current.reset());
    expect(result.current.config).toEqual(DEFAULT_CONFIG);
    expect(storage.getItem(KEY_A)).toBeNull();
  });

  it("restores a stored config on mount for the same identity (AC3)", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
        hidden: ["builtin:service"],
      }),
    );
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result)).toEqual([
      "Timestamp",
      "Model",
      "Provider",
      "Application",
      "Status",
      "Latency",
      "Cost",
      "Tokens",
      "Session ID",
      "tenant",
    ]);
  });

  it("never reuses another identity's config or names at any render (AC4)", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
        hidden: [],
      }),
    );
    const observed = [];
    const { result, rerender } = renderHook(() => {
      const value = useRequestColumns();
      observed.push(value.columns.map((c) => c.id).join(","));
      return value;
    });
    expect(result.current.columns.map((c) => c.id)).toContain(
      "metadata:tenant",
    );

    orgState.currentOrganizationId = "org-2";
    declarationsState.data = [];
    const renderCountBefore = observed.length;
    rerender();

    expect(result.current.storageKey).toBe(KEY_B);
    expect(result.current.columns.map((c) => c.id)).not.toContain(
      "metadata:tenant",
    );
    expect(result.current.pickerEntries.custom).toEqual([]);
    expect(result.current.stale).toEqual([]);
    observed.slice(renderCountBefore).forEach((ids) => {
      expect(ids).not.toContain("metadata:tenant");
    });
    expect(storage.getItem(KEY_A)).toContain("metadata:tenant");
  });

  it("ignores declarations from another organization (AC4)", () => {
    declarationsState.data = [
      { name: "tenant", organization: "org-2" },
      { name: "feature", organization: "org-1" },
    ];
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.pickerEntries.custom.map((e) => e.id)).toEqual([
      "metadata:feature",
    ]);
  });

  it("does not touch storage until identity and organization are ready (J4)", () => {
    orgState.isReady = false;
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.storageKey).toBeNull();
    expect(labels(result)).toHaveLength(10);
    expect(storage.getItem).not.toHaveBeenCalled();
    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("falls back to defaults on a corrupt record without rewriting it (AC12)", () => {
    storage.setItem(KEY_A, "{corrupt");
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result)).toHaveLength(10);
    expect(result.current.storageNotice).toBeNull();
    expect(storage.getItem(KEY_A)).toBe("{corrupt");
  });

  it("keeps working in memory and shows a notice when the write fails (R16/AC12)", () => {
    storage.setItem.mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");
    expect(result.current.storageNotice).toBe("Preferences could not be saved");
    act(() => result.current.dismissStorageNotice());
    expect(result.current.storageNotice).toBeNull();
  });

  it("hides custom columns while declarations are pending or failed and keeps the saved config (AC18)", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
        hidden: [],
      }),
    );
    declarationsState.status = "pending";
    declarationsState.isPending = true;
    declarationsState.data = undefined;
    const { result, rerender } = renderHook(() => useRequestColumns());
    expect(labels(result)).not.toContain("tenant");
    expect(result.current.declarationStatus).toBe("pending");
    expect(result.current.unavailableCustomCount).toBe(1);

    declarationsState.status = "error";
    declarationsState.isPending = false;
    declarationsState.isError = true;
    rerender();
    expect(labels(result)).not.toContain("tenant");
    expect(result.current.declarationStatus).toBe("error");

    act(() => result.current.retryDeclarations());
    expect(declarationsState.refetch).toHaveBeenCalledTimes(1);

    declarationsState.status = "success";
    declarationsState.isError = false;
    declarationsState.data = [{ name: "tenant", organization: "org-1" }];
    rerender();
    expect(labels(result)).toContain("tenant");
    expect(JSON.parse(storage.getItem(KEY_A)).columns).toContainEqual({
      id: "metadata:tenant",
    });
  });

  it("reports stale ids after a successful fetch and removes one on request (AC14)", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:old" }],
        hidden: [],
      }),
    );
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.stale).toEqual(["metadata:old"]);
    expect(labels(result)).not.toContain("old");
    act(() => result.current.remove("metadata:old"));
    expect(result.current.stale).toEqual([]);
    expect(storage.getItem(KEY_A)).not.toContain("metadata:old");
  });
});
