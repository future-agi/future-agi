import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useRequestColumns from "../useRequestColumns";
import { DEFAULT_CONFIG, MAX_COLUMN_ENTRIES } from "../columnModel";

const authState = { user: { id: "user-a" } };
const orgState = { currentOrganizationId: "org-1", isReady: true };
const declarationsState = {
  data: [{ name: "tenant", organization: "org-1" }],
  status: "success",
  isFetching: false,
  refetch: vi.fn(),
};

vi.mock("src/auth/hooks/use-auth-context", () => ({
  useAuthContext: () => authState,
}));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => orgState,
}));

const { mockUseCustomProperties, mockEnqueueSnackbar } = vi.hoisted(() => ({
  mockUseCustomProperties: vi.fn(),
  mockEnqueueSnackbar: vi.fn(),
}));

vi.mock("../../../custom-properties/hooks/useCustomProperties", () => ({
  useCustomProperties: (...args) => mockUseCustomProperties(...args),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
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

const DEFAULT_LABELS = [
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
];

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
    declarationsState.isFetching = false;
    declarationsState.isPlaceholderData = false;
    declarationsState.refetch = vi.fn();
    mockUseCustomProperties.mockReset();
    mockUseCustomProperties.mockReturnValue(declarationsState);
    mockEnqueueSnackbar.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the ten default columns with no stored config", () => {
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
    expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("queries declarations only while enabled and reports their failure itself", () => {
    const { rerender } = renderHook(
      ({ enabled }) => useRequestColumns({ enabled }),
      { initialProps: { enabled: false } },
    );
    expect(mockUseCustomProperties).toHaveBeenLastCalledWith(
      {},
      { enabled: false, meta: { errorHandled: true } },
    );
    rerender({ enabled: true });
    expect(mockUseCustomProperties).toHaveBeenLastCalledWith(
      {},
      { enabled: true, meta: { errorHandled: true } },
    );
  });

  it("toggle, move and reset update columns and persist ids only", () => {
    const { result } = renderHook(() => useRequestColumns());

    act(() => result.current.toggle("metadata:tenant"));
    expect(labels(result).slice(-1)).toEqual(["tenant"]);
    const saved = JSON.parse(storage.getItem(KEY_A));
    expect(saved.columns.slice(-1)).toEqual([{ id: "metadata:tenant" }]);
    expect(JSON.stringify(saved)).not.toContain("acme");

    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");

    // Hidden Provider keeps its slot, so one step passes it and a second
    // step passes Application.
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

  it("adopts the layout another tab saves for the same key, so this tab's next edit keeps it", () => {
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("builtin:provider"));
    const otherTab = JSON.stringify({
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: [],
    });
    const saveInOtherTab = (key, value) => {
      if (value === null) storage.removeItem(key);
      else storage.setItem(key, value);
      act(() => {
        window.dispatchEvent(
          new StorageEvent("storage", { key, newValue: value }),
        );
      });
    };

    saveInOtherTab(KEY_B, otherTab);
    expect(labels(result)).not.toContain("Provider");

    saveInOtherTab(KEY_A, otherTab);
    expect(labels(result)).toEqual([...DEFAULT_LABELS, "tenant"]);
    act(() => result.current.toggle("builtin:service"));
    expect(JSON.parse(storage.getItem(KEY_A))).toEqual({
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: ["builtin:service"],
    });

    saveInOtherTab(KEY_A, null);
    expect(result.current.config).toEqual(DEFAULT_CONFIG);
  });

  it("restores a stored config on mount for the same identity", () => {
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

  it("never reuses another identity's config or names at any render", () => {
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

  it("ignores declarations from another organization", () => {
    declarationsState.data = [
      { name: "tenant", organization: "org-2" },
      { name: "feature", organization: "org-1" },
    ];
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.pickerEntries.custom.map((e) => e.id)).toEqual([
      "metadata:feature",
    ]);
  });

  it("tells the picker when only reserved names are declared and when the column cap is hit", () => {
    declarationsState.data = [
      { name: "application", organization: "org-1" },
      { name: "service", organization: "org-1" },
    ];
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [
          ...DEFAULT_CONFIG.columns,
          ...Array.from({ length: MAX_COLUMN_ENTRIES - 10 }, (_, i) => ({
            id: `metadata:p${i}`,
          })),
        ],
        hidden: [],
      }),
    );
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.pickerEntries.custom).toEqual([]);
    expect(result.current.reservedDeclared).toBe(true);
    expect(result.current.atCap).toBe(true);
  });

  it("does not touch storage until identity and organization are ready", () => {
    orgState.isReady = false;
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.storageKey).toBeNull();
    expect(labels(result)).toHaveLength(10);
    expect(storage.getItem).not.toHaveBeenCalled();
    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it("falls back to defaults on a corrupt record without rewriting it", () => {
    storage.setItem(KEY_A, "{corrupt");
    const { result } = renderHook(() => useRequestColumns());
    expect(labels(result)).toHaveLength(10);
    expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
    expect(storage.getItem(KEY_A)).toBe("{corrupt");
  });

  it.each([
    [
      "duplicate hidden ids",
      {
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
        hidden: ["builtin:provider", "builtin:provider"],
      },
    ],
    [
      "a 64-entry record lacking a built-in",
      {
        v: 1,
        columns: [
          ...DEFAULT_CONFIG.columns.slice(0, -1),
          { id: "metadata:tenant" },
          ...Array.from({ length: MAX_COLUMN_ENTRIES - 10 }, (_, i) => ({
            id: `metadata:p${i}`,
          })),
        ],
        hidden: ["builtin:provider"],
      },
    ],
  ])("reads %s back as the defaults without rewriting it", (_label, record) => {
    const bytes = JSON.stringify(record);
    storage.setItem(KEY_A, bytes);
    storage.setItem.mockClear();
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.config).toEqual(DEFAULT_CONFIG);
    expect(labels(result)).toEqual(DEFAULT_LABELS);
    expect(mockEnqueueSnackbar).not.toHaveBeenCalled();
    expect(storage.getItem(KEY_A)).toBe(bytes);
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("keeps working in memory and shows a notice when the write fails", () => {
    storage.setItem.mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("builtin:provider"));
    expect(labels(result)).not.toContain("Provider");
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Preferences could not be saved",
      { variant: "warning" },
    );
  });

  it("resets in memory and shows a notice when removing the record fails", () => {
    const { result } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("builtin:provider"));
    storage.removeItem.mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    act(() => result.current.reset());
    expect(labels(result)).toEqual(DEFAULT_LABELS);
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith(
      "Preferences could not be saved",
      { variant: "warning" },
    );
  });

  it("hides custom columns while declarations are pending or failed and keeps the saved config", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        v: 1,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
        hidden: [],
      }),
    );
    declarationsState.status = "pending";
    declarationsState.data = undefined;
    const { result, rerender } = renderHook(() => useRequestColumns());
    expect(labels(result)).not.toContain("tenant");
    expect(result.current.declarationStatus).toBe("pending");
    expect(result.current.unavailableCustomCount).toBe(1);

    declarationsState.status = "error";
    rerender();
    expect(labels(result)).not.toContain("tenant");
    expect(result.current.declarationStatus).toBe("error");

    act(() => result.current.retryDeclarations());
    expect(declarationsState.refetch).toHaveBeenCalledTimes(1);
    expect(result.current.declarationsFetching).toBe(false);
    declarationsState.isFetching = true;
    rerender();
    expect(result.current.declarationsFetching).toBe(true);

    declarationsState.status = "success";
    declarationsState.data = [{ name: "tenant", organization: "org-1" }];
    rerender();
    expect(labels(result)).toContain("tenant");
    expect(JSON.parse(storage.getItem(KEY_A)).columns).toContainEqual({
      id: "metadata:tenant",
    });
  });

  it("keeps cached declarations and editing usable after a failed background refresh", () => {
    declarationsState.data = [
      { name: "tenant", organization: "org-1" },
      { name: "feature", organization: "org-1" },
    ];
    const { result, rerender } = renderHook(() => useRequestColumns());
    act(() => result.current.toggle("metadata:tenant"));
    act(() => result.current.toggle("metadata:feature"));
    const stored = storage.getItem(KEY_A);
    storage.setItem.mockClear();

    declarationsState.status = "error";
    rerender();
    expect(labels(result)).toEqual([...DEFAULT_LABELS, "tenant", "feature"]);
    expect(result.current.declarationsAvailable).toBe(true);
    expect(result.current.declarationStatus).toBe("error");
    expect(result.current.unavailableCustomCount).toBe(0);
    expect(result.current.pickerEntries.custom).toHaveLength(2);
    expect(storage.getItem(KEY_A)).toBe(stored);
    expect(storage.setItem).not.toHaveBeenCalled();

    act(() => result.current.move("metadata:feature", -1));
    expect(labels(result).slice(-2)).toEqual(["feature", "tenant"]);
    act(() => result.current.toggle("metadata:tenant"));
    expect(labels(result)).not.toContain("tenant");
    declarationsState.isFetching = true;
    rerender();
    expect(labels(result)).toContain("feature");
    expect(result.current.declarationsFetching).toBe(true);
  });

  it("treats an empty cached declaration list as available after refresh failure", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        ...DEFAULT_CONFIG,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      }),
    );
    declarationsState.data = [];
    declarationsState.status = "error";
    const { result } = renderHook(() => useRequestColumns());
    expect(result.current.declarationsAvailable).toBe(true);
    expect(result.current.unavailableCustomCount).toBe(0);
    expect(result.current.stale).toEqual(["metadata:tenant"]);
    expect(labels(result)).toEqual(DEFAULT_LABELS);
  });

  it("does not use placeholder declarations or declarations before organization readiness", () => {
    storage.setItem(
      KEY_A,
      JSON.stringify({
        ...DEFAULT_CONFIG,
        columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      }),
    );
    declarationsState.isPlaceholderData = true;
    const { result, rerender } = renderHook(() => useRequestColumns());
    expect(result.current.declarationsAvailable).toBe(false);
    expect(result.current.pickerEntries.custom).toEqual([]);
    expect(labels(result)).not.toContain("tenant");
    declarationsState.isPlaceholderData = false;
    orgState.isReady = false;
    rerender();
    expect(result.current.declarationsAvailable).toBe(false);
    expect(result.current.pickerEntries.custom).toEqual([]);
  });

  describe("moving past an undeclared saved id", () => {
    const customIds = (result) =>
      result.current.pickerEntries.custom.map((e) => e.id);
    const record = JSON.stringify({
      v: 1,
      columns: [
        ...DEFAULT_CONFIG.columns,
        { id: "metadata:a" },
        { id: "metadata:deleted" },
        { id: "metadata:b" },
      ],
      hidden: [],
    });

    beforeEach(() => {
      storage.setItem(KEY_A, record);
      declarationsState.data = [
        { name: "a", organization: "org-1" },
        { name: "b", organization: "org-1" },
      ];
    });

    it("reorders the two declared columns in one click each way and keeps the stale id until Remove", () => {
      const { result } = renderHook(() => useRequestColumns());
      expect(customIds(result)).toEqual(["metadata:a", "metadata:b"]);
      expect(result.current.stale).toEqual(["metadata:deleted"]);

      act(() => result.current.move("metadata:a", 1));
      expect(customIds(result)).toEqual(["metadata:b", "metadata:a"]);
      expect(labels(result).slice(-2)).toEqual(["b", "a"]);
      expect(result.current.stale).toEqual(["metadata:deleted"]);
      expect(
        JSON.parse(storage.getItem(KEY_A))
          .columns.slice(-3)
          .map((c) => c.id),
      ).toEqual(["metadata:b", "metadata:deleted", "metadata:a"]);

      act(() => result.current.move("metadata:a", -1));
      expect(customIds(result)).toEqual(["metadata:a", "metadata:b"]);
      expect(storage.getItem(KEY_A)).toBe(record);

      act(() => result.current.remove("metadata:deleted"));
      expect(result.current.stale).toEqual([]);
      expect(storage.getItem(KEY_A)).not.toContain("metadata:deleted");
    });

    it("moves no custom column while declarations are pending or failed", () => {
      declarationsState.status = "pending";
      declarationsState.data = undefined;
      const { result, rerender } = renderHook(() => useRequestColumns());
      act(() => result.current.move("metadata:a", 1));
      expect(storage.getItem(KEY_A)).toBe(record);

      declarationsState.status = "error";
      rerender();
      act(() => result.current.move("metadata:b", -1));
      expect(storage.getItem(KEY_A)).toBe(record);
      expect(result.current.config.columns.slice(-3).map((c) => c.id)).toEqual([
        "metadata:a",
        "metadata:deleted",
        "metadata:b",
      ]);

      declarationsState.status = "success";
      declarationsState.data = [
        { name: "a", organization: "org-1" },
        { name: "b", organization: "org-1" },
      ];
      rerender();
      expect(customIds(result)).toEqual(["metadata:a", "metadata:b"]);
    });
  });

  it("reports stale ids after a successful fetch and removes one on request", () => {
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
