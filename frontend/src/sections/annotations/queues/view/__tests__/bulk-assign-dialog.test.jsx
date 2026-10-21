import React, { useState, useEffect } from "react";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { describe, test, expect } from "vitest";
import { Button, Stack, DialogContent } from "@mui/material";

// This component mirrors the bulk-assign section of QueueDetailView.
// QueueDetailView pulls in too many real dependencies (routing, queries, auth
// context, API hooks) to render cleanly in a unit test, so we replicate just
// the relevant state and UI here to keep the tests fast and focused.
/* eslint-disable react/prop-types -- test helper, prop-types not worth the boilerplate here */
function TestBulkAssignDialog({
  initialAnnotators = [],
  initialOpen = true,
  initialLoading = false,
}) {
  const [queueAnnotators, setQueueAnnotators] = useState(initialAnnotators);
  const [bulkAssignOpen, setBulkAssignOpen] = useState(initialOpen);
  const [bulkAssignUserIds, setBulkAssignUserIds] = useState(new Set());
  const [isAssigningItems] = useState(initialLoading);

  useEffect(() => {
    if (!bulkAssignOpen) return;
    const annotatorIds = new Set(queueAnnotators.map((a) => String(a.user_id)));
    setBulkAssignUserIds((prev) => {
      const next = new Set();
      for (const id of prev) if (annotatorIds.has(id)) next.add(id);
      return next;
    });
  }, [bulkAssignOpen, queueAnnotators]);

  const toggleAll = () => {
    const allUserIds = new Set(queueAnnotators.map((a) => String(a.user_id)));
    const allSelected = bulkAssignUserIds.size === queueAnnotators.length;
    setBulkAssignUserIds(allSelected ? new Set() : allUserIds);
  };

  return (
    <div>
      <button type="button" onClick={() => setBulkAssignOpen(true)}>
        Open
      </button>
      <DialogContent>
        {queueAnnotators.length > 1 && (
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            <Button
              size="small"
              onClick={toggleAll}
              disabled={isAssigningItems}
            >
              {bulkAssignUserIds.size === queueAnnotators.length
                ? "Deselect All"
                : "Select All"}
            </Button>
            <Button
              size="small"
              onClick={() => setBulkAssignUserIds(new Set())}
              disabled={isAssigningItems}
            >
              Select None
            </Button>
          </Stack>
        )}
      </DialogContent>
      {/* Expose test helpers */}
      <Button
        onClick={() => setQueueAnnotators((prev) => prev.slice(0, 1))}
        data-testid="shrink-annotators"
      >
        Shrink
      </Button>
      <div data-testid="selected-count">{bulkAssignUserIds.size}</div>
    </div>
  );
}

describe("Bulk assign dialog select-all behavior", () => {
  test("shows toggle when 2+ annotators and toggles between select/deselect", async () => {
    const annotators = [
      { user_id: 1, name: "A" },
      { user_id: 2, name: "B" },
    ];

    render(
      <TestBulkAssignDialog
        initialAnnotators={annotators}
        initialOpen={true}
      />,
    );

    // Buttons should be visible
    const toggle = screen.getByRole("button", {
      name: /Select All|Deselect All/,
    });
    expect(toggle).toBeInTheDocument();

    // Click select all -> both selected
    await userEvent.click(toggle);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("2");

    // Now label should switch to Deselect All
    expect(toggle).toHaveTextContent("Deselect All");

    // Click deselect
    await userEvent.click(toggle);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("0");
    expect(toggle).toHaveTextContent("Select All");
  });

  test("buttons disabled when loading", async () => {
    const annotators = [
      { user_id: 1, name: "A" },
      { user_id: 2, name: "B" },
    ];

    render(
      <TestBulkAssignDialog
        initialAnnotators={annotators}
        initialLoading={true}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /Select All|Deselect All/,
    });
    const none = screen.getByRole("button", { name: /Select None/ });

    expect(toggle).toBeDisabled();
    expect(none).toBeDisabled();
  });

  test("does not show bulk buttons when only 1 annotator", () => {
    const annotators = [{ user_id: 1, name: "A" }];

    render(
      <TestBulkAssignDialog
        initialAnnotators={annotators}
        initialOpen={true}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /Select All|Deselect All/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Select None/ }),
    ).not.toBeInTheDocument();
  });

  test("select none clears all selections", async () => {
    const annotators = [
      { user_id: 1, name: "A" },
      { user_id: 2, name: "B" },
    ];

    render(
      <TestBulkAssignDialog
        initialAnnotators={annotators}
        initialOpen={true}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /Select All|Deselect All/,
    });
    await userEvent.click(toggle);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("2");

    const none = screen.getByRole("button", { name: /Select None/ });
    await userEvent.click(none);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("0");
    expect(toggle).toHaveTextContent("Select All");
  });

  test("shrinking annotators removes selections not present", async () => {
    const annotators = [
      { user_id: 1, name: "A" },
      { user_id: 2, name: "B" },
      { user_id: 3, name: "C" },
    ];

    render(
      <TestBulkAssignDialog
        initialAnnotators={annotators}
        initialOpen={true}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /Select All|Deselect All/,
    });
    await userEvent.click(toggle);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("3");

    // Shrink annotators to 1 — effect should remove selections
    await userEvent.click(screen.getByTestId("shrink-annotators"));

    // After shrinking to the first annotator (user_id 1), selection should include it, so expect 1
    expect(screen.getByTestId("selected-count")).toHaveTextContent("1");
  });
});
