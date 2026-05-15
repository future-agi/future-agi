import { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import LabelPicker from "../label-picker";

const refetchMock = vi.fn();

vi.mock("src/api/annotation-labels/annotation-labels", () => ({
  useAnnotationLabelsList: () => ({
    data: {
      results: [
        {
          id: "existing-label",
          name: "Existing Label",
          type: "categorical",
        },
      ],
    },
    refetch: refetchMock,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/sections/annotations/labels/create-label-drawer", () => ({
  default: ({ open, onClose, onCreated }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          onCreated?.({
            id: "new-label",
            name: "Newly Created Label",
            type: "text",
          });
          onClose();
        }}
      >
        Finish label creation
      </button>
    ) : null,
}));

describe("LabelPicker", () => {
  beforeEach(() => {
    refetchMock.mockClear();
  });

  it("auto-selects a newly created label", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    function Harness() {
      const [selectedIds, setSelectedIds] = useState(["existing-label"]);
      return (
        <LabelPicker
          selectedIds={selectedIds}
          onChange={(ids) => {
            onChange(ids);
            setSelectedIds(ids);
          }}
        />
      );
    }

    render(<Harness />);

    await user.click(screen.getByRole("button", { name: /create new label/i }));
    await user.click(
      screen.getByRole("button", { name: /finish label creation/i }),
    );

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(["existing-label", "new-label"]);
    });
    expect(screen.getAllByText("Newly Created Label").length).toBeGreaterThan(
      0,
    );
    expect(refetchMock).toHaveBeenCalled();
  });
});
