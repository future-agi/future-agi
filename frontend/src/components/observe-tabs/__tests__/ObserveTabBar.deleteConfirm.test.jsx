import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import React from "react";
import { enqueueSnackbar } from "notistack";
import ObserveTabBar from "../ObserveTabBar";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";

// The "x" shown on a hovered saved-view tab deletes the view. It must ask
// first, like the tab's right-click Delete, and report a failed delete.
const mockDeleteView = vi.fn();
let mockSavedViewsList = [];
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: () => false,
}));
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: mockSavedViewsList } }),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: mockDeleteView }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
  // Mirror of the real helper (the factory replaces the whole module).
  getOwnViewNames: (views, userId) =>
    (views ?? [])
      .filter((v) => userId && String(v.created_by?.id) === String(userId))
      .map((v) => v.name),
}));

const SHARED_VIEW = {
  id: "v1",
  name: "Errors",
  tab_type: "traces",
  visibility: "project",
};
const PERSONAL_VIEW = {
  id: "v2",
  name: "Slow spans",
  tab_type: "spans",
  visibility: "personal",
};

const renderBar = ({ activeTab = "traces", onTabChange = () => {} } = {}) => {
  window.history.pushState({}, "Test page", "/");
  return render(
    <ObserveHeaderContext.Provider
      value={{
        headerConfig: {},
        setHeaderConfig: () => {},
        activeViewConfig: null,
        setActiveViewConfig: () => {},
        registerGetViewConfig: () => {},
        getViewConfig: () => ({}),
      }}
    >
      <ObserveTabBar
        projectId="p1"
        activeTab={activeTab}
        onTabChange={onTabChange}
      />
    </ObserveHeaderContext.Provider>,
  );
};

// Clicks the hover "x" of the tab showing this view name.
const clickTabX = (name) => {
  const tab = screen.getByText(name).closest("button");
  const x = tab?.querySelector(".close-btn");
  if (!x) throw new Error(`no close icon on the "${name}" tab`);
  fireEvent.click(x);
};

const lastDeleteCallbacks = () => {
  expect(mockDeleteView).toHaveBeenCalledTimes(1);
  return mockDeleteView.mock.calls[0][1] ?? {};
};

describe("ObserveTabBar — the tab x asks before deleting a saved view", () => {
  beforeEach(() => {
    mockDeleteView.mockReset();
    vi.mocked(enqueueSnackbar).mockReset();
    mockSavedViewsList = [SHARED_VIEW, PERSONAL_VIEW];
  });

  it("does not delete on click; it opens a confirmation instead", () => {
    renderBar();
    clickTabX("Errors");

    expect(mockDeleteView).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete “Errors”?")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/This view will be permanently removed\./),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/This action cannot be undone\./),
    ).toBeInTheDocument();
  });

  it("warns that a shared view is removed for all team members", () => {
    renderBar();
    clickTabX("Errors");

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(
        /This will remove the view for all team members\./,
      ),
    ).toBeInTheDocument();
  });

  it("gives no team warning for a personal view", () => {
    renderBar();
    clickTabX("Slow spans");

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Delete “Slow spans”?"),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/all team members/)).toBeNull();
  });

  it("keeps the view when the confirmation is cancelled", async () => {
    renderBar();
    clickTabX("Errors");

    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mockDeleteView).not.toHaveBeenCalled();
  });

  it("deletes the view on confirm and leaves its tab when it was open", async () => {
    const onTabChange = vi.fn();
    renderBar({ activeTab: "view-v1", onTabChange });
    clickTabX("Errors");

    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete",
      }),
    );

    const { onSuccess } = lastDeleteCallbacks();
    expect(mockDeleteView.mock.calls[0][0]).toBe("v1");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    act(() => onSuccess());
    expect(onTabChange).toHaveBeenCalledWith("traces");
  });

  it("stays on the current tab after deleting another view", () => {
    const onTabChange = vi.fn();
    renderBar({ activeTab: "view-v1", onTabChange });
    clickTabX("Slow spans");

    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete",
      }),
    );

    const { onSuccess } = lastDeleteCallbacks();
    expect(mockDeleteView.mock.calls[0][0]).toBe("v2");
    act(() => onSuccess());
    expect(onTabChange).not.toHaveBeenCalled();
  });

  it("reports a failed delete", () => {
    renderBar();
    clickTabX("Errors");

    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Delete",
      }),
    );

    const { onError } = lastDeleteCallbacks();
    expect(typeof onError).toBe("function");
    act(() => onError({}));
    expect(enqueueSnackbar).toHaveBeenCalledWith("Failed to delete view", {
      variant: "error",
    });

    act(() =>
      onError({
        response: {
          status: 403,
          data: {
            detail: "You do not have permission to perform this action.",
          },
        },
      }),
    );
    expect(enqueueSnackbar).toHaveBeenLastCalledWith(
      "You do not have permission to perform this action.",
      { variant: "error" },
    );
  });
});
