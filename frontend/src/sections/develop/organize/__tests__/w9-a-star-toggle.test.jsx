// @vitest-environment jsdom
// W9 A4 (W9-SCOPE §5): the star toggle posts / deletes the per-user star,
// flips at once, says what it does, and goes back with the server's
// sentence when refused.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const post = vi.fn();
const del = vi.fn();
vi.mock("src/utils/axios", () => ({
  default: { post: (...a) => post(...a), delete: (...a) => del(...a) },
  endpoints: {
    develop: { w9: { datasetStar: (id) => `/star/${id}/` } },
  },
}));
const notify = vi.fn();
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...a) => notify(...a),
  default: (...a) => notify(...a),
}));

const { default: DatasetStarToggle } = await import("../DatasetStarToggle");

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );

beforeEach(() => {
  post.mockReset();
  del.mockReset();
  notify.mockReset();
});
afterEach(cleanup);

describe("DatasetStarToggle", () => {
  it("stars: POST, pressed, and the click does not open the row", async () => {
    post.mockResolvedValue({
      data: { result: { dataset_id: "d1", starred: true } },
    });
    const onRow = vi.fn();
    wrap(
      <div onClick={onRow}>
        <DatasetStarToggle
          dataset={{ id: "d1", name: "Golden", starred: false }}
        />
      </div>,
    );
    const btn = screen.getByRole("button", { name: "Star Golden" });
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(post).toHaveBeenCalledWith("/star/d1/"));
    expect(onRow).not.toHaveBeenCalled();
  });

  it("unstars with DELETE", async () => {
    del.mockResolvedValue({
      data: { result: { dataset_id: "d1", starred: false } },
    });
    wrap(
      <DatasetStarToggle
        dataset={{ id: "d1", name: "Golden", starred: true }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Star Golden" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/star/d1/"));
    expect(
      screen
        .getByRole("button", { name: "Star Golden" })
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("goes back and shows the server's sentence when refused", async () => {
    post.mockRejectedValue({
      response: { status: 404, data: { result: "Dataset not found" } },
    });
    wrap(
      <DatasetStarToggle
        dataset={{ id: "gone", name: "Old", starred: false }}
      />,
    );
    const btn = screen.getByRole("button", { name: "Star Old" });
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("false"));
    expect(notify).toHaveBeenCalledWith("Dataset not found", {
      variant: "error",
      key: "dataset-star",
    });
  });
});
