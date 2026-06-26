import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, waitFor } from "src/utils/test-utils";
import { enqueueSnackbar } from "src/components/snackbar";
import DatapointCard from "../DatapointCard";

vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

const baseValue = {
  cellValue: "value to copy",
};

const baseColumn = {
  headerName: "Plain column",
  dataType: "text",
  originType: "OTHERS",
};

const renderCard = (props = {}) =>
  render(
    <DatapointCard
      value={baseValue}
      column={baseColumn}
      allowCopy
      indColsDifTracker={{}}
      {...props}
    />,
  );

const getCopyIcons = (container) => container.querySelectorAll('[alt="Copy"]');

describe("DatapointCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it("renders a copy icon for cards without tabs", async () => {
    const { container } = renderCard({ showTabs: false });

    const copyIcons = getCopyIcons(container);
    expect(copyIcons).toHaveLength(1);

    fireEvent.click(copyIcons[0]);

    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        "value to copy",
      );
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith("Copied to clipboard", {
      variant: "success",
    });
  });

  it("keeps the existing copy icon for cards with tabs", () => {
    const { container } = renderCard({ showTabs: true });

    expect(getCopyIcons(container)).toHaveLength(1);
  });
});
