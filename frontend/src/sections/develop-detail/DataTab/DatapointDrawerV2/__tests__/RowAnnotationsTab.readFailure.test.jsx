import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";

const h = vi.hoisted(() => ({ read: vi.fn(), Null: () => null }));
vi.mock("src/generated/api-contracts/api", () => ({
  modelHubScoresForSource: h.read,
  modelHubScoresBulkCreate: vi.fn(),
  modelHubScoresCreate: vi.fn(),
  modelHubScoresDelete: vi.fn(),
}));
vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: h.Null,
}));
vi.mock("src/components/traceDetailDrawer/AddLabelDrawer", () => ({
  default: h.Null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotationSidebarContent", () => ({
  default: h.Null,
}));
vi.mock("src/components/iconify", () => ({ default: h.Null }));
vi.mock("src/sections/common/ux/oneview/Section", () => ({
  default: ({ children }) => children,
}));

import RowAnnotationsTab from "../RowAnnotationsTab";

const ROW = "00000000-0000-4000-8000-000000000001";
const SCORE = {
  id: "00000000-0000-4000-8000-000000000002",
  source_type: "dataset_row",
  source_id: ROW,
  label_id: "00000000-0000-4000-8000-000000000003",
  label_name: "Review quality",
  label_type: "thumbs_up_down",
  label_settings: {},
  value: { value: "up" },
  score_source: "human",
  notes: "Checked the expected answer",
  annotator_name: "Reviewer",
  annotator_email: "reviewer@example.test",
  updated_at: "2026-01-01T12:00:00Z",
  queue_id: "queue-a",
  queue_item: "00000000-0000-4000-8000-000000000004",
};
let client;
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <RowAnnotationsTab
        datasetId="dataset-a"
        rowId={ROW}
        canEdit
        onAddToQueue={() => {}}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  h.read.mockReset();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

it("shows a read error instead of claiming no annotations, then retries", async () => {
  h.read.mockRejectedValueOnce(new Error("Read unavailable"));
  h.read.mockResolvedValueOnce({ status: true, result: [SCORE] });
  mount();
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not load annotations.Retry",
  );
  expect(screen.queryByText("No annotations yet")).toBeNull();
  expect(h.read.mock.calls).toEqual([
    [{ source_type: "dataset_row", source_id: ROW }],
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect((await screen.findByText("Review quality")).textContent).toBe(
    "Review quality",
  );
  expect(screen.getByText("Up").textContent).toBe("Up");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(h.read).toHaveBeenCalledTimes(2);
});

it("retains loaded annotations and warns when a refresh fails", async () => {
  h.read.mockResolvedValueOnce({ status: true, result: [SCORE] });
  h.read.mockRejectedValueOnce(new Error("Refresh unavailable"));
  mount();
  await screen.findByText("Review quality");
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ["scores", "dataset_row", ROW],
    });
  });
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Couldn't refresh annotations. Showing the last loaded annotations.Retry",
  );
  expect(screen.getByText("Review quality").textContent).toBe("Review quality");
  expect(screen.getByText("Up").textContent).toBe("Up");
  expect(screen.queryByText("No annotations yet")).toBeNull();
  expect(client.getQueryData(["scores", "dataset_row", ROW])).toEqual({
    status: true,
    result: [SCORE],
  });
});

it("shows the empty state only after a successful empty read", async () => {
  h.read.mockResolvedValueOnce({ status: true, result: [] });
  mount();
  await waitFor(() => {
    expect(screen.getByText("No annotations yet").textContent).toBe(
      "No annotations yet",
    );
  });
  expect(screen.queryByRole("alert")).toBeNull();
});
