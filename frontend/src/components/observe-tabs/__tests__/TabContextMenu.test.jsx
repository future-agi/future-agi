import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import TabContextMenu from "../TabContextMenu";

const mockUpdateView = vi.fn();
const mockDeleteView = vi.fn();
const mockDuplicateView = vi.fn();

vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: mockUpdateView }),
  useDeleteSavedView: () => ({ mutateAsync: mockDeleteView }),
  useDuplicateSavedView: () => ({ mutateAsync: mockDuplicateView }),
}));

const defaultProps = {
  anchorPosition: { x: 32, y: 48 },
  view: {
    id: "view-1",
    name: "Trace triage",
    visibility: "personal",
  },
  projectId: "project-1",
  onClose: vi.fn(),
  onRename: vi.fn(),
  onTabChange: vi.fn(),
};

const renderMenu = (overrides = {}) =>
  render(<TabContextMenu {...defaultProps} {...overrides} />);

describe("TabContextMenu", () => {
  beforeEach(() => {
    mockUpdateView.mockReset();
    mockDeleteView.mockReset();
    mockDuplicateView.mockReset();
    defaultProps.onClose.mockReset();
    defaultProps.onRename.mockReset();
    defaultProps.onTabChange.mockReset();
    mockDuplicateView.mockResolvedValue({
      data: { result: { id: "duplicated-view" } },
    });
    mockDeleteView.mockResolvedValue({ data: { result: { message: "ok" } } });
  });

  it("navigates to the duplicated view after closing the menu", async () => {
    renderMenu();

    fireEvent.click(screen.getByRole("menuitem", { name: /duplicate/i }));

    expect(defaultProps.onClose).toHaveBeenCalled();
    expect(mockDuplicateView).toHaveBeenCalledWith({ id: "view-1" });
    await waitFor(() =>
      expect(defaultProps.onTabChange).toHaveBeenCalledWith(
        "view-duplicated-view",
      ),
    );
  });

  it("navigates to traces after confirming delete", async () => {
    renderMenu();

    fireEvent.click(screen.getByRole("menuitem", { name: /^delete$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    expect(defaultProps.onClose).toHaveBeenCalled();
    expect(mockDeleteView).toHaveBeenCalledWith("view-1");
    await waitFor(() =>
      expect(defaultProps.onTabChange).toHaveBeenCalledWith("traces"),
    );
  });
});
