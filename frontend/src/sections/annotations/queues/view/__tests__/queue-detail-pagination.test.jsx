import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import QueueItemsTable from "../../items/queue-items-table";

/**
 * Pagination replaced with infinite scroll.
 * These tests validate QueueItemsTable's footer status display.
 */

const makeItems = (count) =>
  Array.from({ length: count }, (_, i) => ({
    id: `item-${i}`,
    status: "pending",
    source_type: "dataset_row",
    source_preview: `Row ${i}`,
    assigned_to_name: null,
    assigned_users: [],
    created_at: "2026-03-18T00:00:00Z",
  }));

describe("QueueItemsTable infinite scroll", () => {
  it("displays item count in footer", () => {
    render(
      <QueueItemsTable
        data={makeItems(10)}
        loading={false}
        totalCount={30}
        selectedIds={new Set()}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText("10 of 30 items")).toBeInTheDocument();
  });

  it("shows 0 of 0 when no items", () => {
    render(
      <QueueItemsTable
        data={[]}
        loading={false}
        totalCount={0}
        selectedIds={new Set()}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText("0 of 0 items")).toBeInTheDocument();
  });
});
