import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import React from "react";
import ObserveTabBar from "../ObserveTabBar";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";

const mockCreateSavedView = vi.fn();
let mockSavedViewsList = [];
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: mockSavedViewsList } }),
  useCreateSavedView: () => ({ mutate: mockCreateSavedView }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
  // Mirror of the real helper (the factory replaces the whole module).
  getOwnViewNames: (views, userId) =>
    (views ?? [])
      .filter((v) => userId && String(v.created_by?.id) === String(userId))
      .map((v) => v.name),
}));

const renderWithCtx = ({
  getViewConfig = () => ({}),
  activeTab = "traces",
  selectedTab,
  projectSource,
  onTabChange = () => {},
} = {}) => {
  // resolveTabType reads selectedTab from the URL (not getTabType).
  window.history.pushState(
    {},
    "Test page",
    selectedTab ? `/?selectedTab=${selectedTab}` : "/",
  );
  return render(
    <ObserveHeaderContext.Provider
      value={{
        headerConfig: {},
        setHeaderConfig: () => {},
        activeViewConfig: null,
        setActiveViewConfig: () => {},
        registerGetViewConfig: () => {},
        getViewConfig,
      }}
    >
      <ObserveTabBar
        projectId="p1"
        activeTab={activeTab}
        onTabChange={onTabChange}
        projectSource={projectSource}
      />
    </ObserveHeaderContext.Provider>,
  );
};

const clickCreateViewButton = () => {
  const btn = document.querySelector("[data-create-view-btn]");
  if (!btn) throw new Error("data-create-view-btn not found");
  fireEvent.click(btn);
};

const typeAndSubmit = async (name) => {
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: name } });
  const saveBtn = screen.getByRole("button", { name: /save|create/i });
  fireEvent.click(saveBtn);
};

describe("ObserveTabBar — Query integration", () => {
  beforeEach(() => {
    mockSavedViewsList = [];
  });

  it.each([undefined, "simulator"])(
    "keeps Query visible for %s projects without shifting saved-view shortcuts",
    (projectSource) => {
      const onTabChange = vi.fn();
      mockSavedViewsList = [
        { id: "saved", name: "Saved traces", tab_type: "traces" },
      ];
      renderWithCtx({ projectSource, onTabChange });
      fireEvent.click(screen.getByRole("button", { name: /^Query/ }));
      expect(onTabChange).toHaveBeenLastCalledWith("query");
      onTabChange.mockClear();
      fireEvent.keyDown(document, { key: "2" });
      if (projectSource === "simulator") {
        expect(onTabChange).not.toHaveBeenCalled();
        expect(screen.queryByRole("button", { name: /^Sessions/ })).toBeNull();
        expect(screen.queryByRole("button", { name: /^Users/ })).toBeNull();
      } else {
        expect(onTabChange).toHaveBeenLastCalledWith("sessions");
        expect(screen.getByRole("button", { name: /^Users/ })).toBeVisible();
      }
      fireEvent.keyDown(document, { key: "4" });
      expect(onTabChange).toHaveBeenLastCalledWith("view-saved");
    },
  );

  it("does not create a trace-list saved view from the SQL tab", () => {
    renderWithCtx({ activeTab: "query" });
    expect(document.querySelector("[data-create-view-btn]")).toBeDisabled();
  });
});

describe("ObserveTabBar — Save View snapshots current filters", () => {
  beforeEach(() => {
    mockCreateSavedView.mockReset();
    mockSavedViewsList = [];
  });

  it("passes ctx.getViewConfig() output as config in createSavedView", async () => {
    const snapshot = {
      filters: [
        {
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "ERROR",
          },
        },
      ],
      display: { viewMode: "grid" },
    };
    renderWithCtx({ getViewConfig: () => snapshot });

    clickCreateViewButton();
    await typeAndSubmit("t1");

    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    const payload = mockCreateSavedView.mock.calls[0][0];
    expect(payload.config).toEqual(snapshot);
    expect(payload.tab_type).toBe("traces");
    expect(payload.name).toBe("t1");
    expect(payload.project_id).toBe("p1");
  });

  it("falls back to {} when getViewConfig returns null", async () => {
    renderWithCtx({ getViewConfig: () => null });

    clickCreateViewButton();
    await typeAndSubmit("t2");

    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].config).toEqual({});
  });
});

describe("ObserveTabBar — resolveTabType", () => {
  beforeEach(() => {
    mockCreateSavedView.mockReset();
    mockSavedViewsList = [];
  });

  it("saves tab_type 'traces' when selectedTab is not 'spans'", async () => {
    renderWithCtx({ activeTab: "traces" });
    clickCreateViewButton();
    await typeAndSubmit("t");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("traces");
  });

  it("saves tab_type 'spans' when selectedTab is 'spans' in the URL", async () => {
    renderWithCtx({ activeTab: "spans", selectedTab: "spans" });
    clickCreateViewButton();
    await typeAndSubmit("s");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });

  it("forces tab_type to 'users' when activeTab is 'users'", async () => {
    renderWithCtx({ activeTab: "users" });
    clickCreateViewButton();
    await typeAndSubmit("u");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("users");
  });

  it("forces tab_type to 'sessions' when activeTab is 'sessions'", async () => {
    renderWithCtx({ activeTab: "sessions" });
    clickCreateViewButton();
    await typeAndSubmit("ss");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("sessions");
  });

  it("inherits the current view's tab_type when activeTab is 'view-<id>'", async () => {
    mockSavedViewsList = [
      { id: "abc", name: "my-spans-view", tab_type: "spans" },
    ];
    renderWithCtx({ activeTab: "view-abc" });
    clickCreateViewButton();
    await typeAndSubmit("copy");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });
});
