import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import React from "react";
import ObserveTabBar from "../ObserveTabBar";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";

const mockCreateSavedView = vi.fn();
let mockSavedViewsList = [];
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { customViews: mockSavedViewsList } }),
  useCreateSavedView: () => ({ mutate: mockCreateSavedView }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
}));

const renderWithCtx = ({
  getViewConfig = () => ({}),
  getTabType = () => "traces",
  activeTab = "traces",
} = {}) =>
  render(
    <ObserveHeaderContext.Provider
      value={{
        headerConfig: {},
        setHeaderConfig: () => {},
        activeViewConfig: null,
        setActiveViewConfig: () => {},
        registerGetViewConfig: () => {},
        getViewConfig,
        registerGetTabType: () => {},
        getTabType,
      }}
    >
      <ObserveTabBar
        projectId="p1"
        activeTab={activeTab}
        onTabChange={() => {}}
      />
    </ObserveHeaderContext.Provider>,
  );

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

describe("ObserveTabBar — Save View snapshots current filters", () => {
  beforeEach(() => {
    mockCreateSavedView.mockReset();
    mockSavedViewsList = [];
  });

  it("passes ctx.getViewConfig() output as config in createSavedView", async () => {
    const snapshot = {
      filters: [{ columnId: "status" }],
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

  it("uses getTabType() when activeTab is 'traces' (returns 'traces' for trace sub-tab)", async () => {
    renderWithCtx({ getTabType: () => "traces", activeTab: "traces" });
    clickCreateViewButton();
    await typeAndSubmit("t");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("traces");
  });

  it("uses getTabType() when activeTab is 'traces' (returns 'spans' for span sub-tab)", async () => {
    renderWithCtx({ getTabType: () => "spans", activeTab: "traces" });
    clickCreateViewButton();
    await typeAndSubmit("s");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });

  it("forces tab_type to 'users' when activeTab is 'users'", async () => {
    renderWithCtx({ getTabType: () => "traces", activeTab: "users" });
    clickCreateViewButton();
    await typeAndSubmit("u");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("users");
  });

  it("forces tab_type to 'sessions' when activeTab is 'sessions'", async () => {
    renderWithCtx({ getTabType: () => "traces", activeTab: "sessions" });
    clickCreateViewButton();
    await typeAndSubmit("ss");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("sessions");
  });

  it("inherits the current view's tab_type when activeTab is 'view-<id>'", async () => {
    mockSavedViewsList = [
      { id: "abc", name: "my-spans-view", tab_type: "spans" },
    ];
    renderWithCtx({ getTabType: () => "traces", activeTab: "view-abc" });
    clickCreateViewButton();
    await typeAndSubmit("copy");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });
});
