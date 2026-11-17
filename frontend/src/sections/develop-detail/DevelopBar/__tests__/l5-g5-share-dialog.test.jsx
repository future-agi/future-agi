// options-truth L5 G5 (w0:public-link): Share shows the dataset's Data-tab
// link with a copy button; no public-link switch, no stored flag.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ShareDatasetDialog, { shareLinkOf } from "../ShareDatasetDialog";

afterEach(cleanup);

describe("ShareDatasetDialog", () => {
  it("builds the Data-tab link", () => {
    expect(shareLinkOf("http://127.0.0.1:5199", "ds-1")).toBe(
      "http://127.0.0.1:5199/dashboard/develop/ds-1?tab=data",
    );
  });

  it("shows the link and copies it, with no switch and no storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(
      <ShareDatasetDialog
        open
        datasetId="ds-42"
        name="Golden"
        onClose={() => {}}
      />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/public link/i)).toBeNull();
    expect(screen.queryByText(/anyone with the link/i)).toBeNull();
    const field = screen.getByLabelText("Dataset link");
    expect(field.value).toBe(
      `${window.location.origin}/dashboard/develop/ds-42?tab=data`,
    );
    fireEvent.click(screen.getByLabelText("Copy link"));
    await vi.waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        `${window.location.origin}/dashboard/develop/ds-42?tab=data`,
      ),
    );
    const keys = setItem.mock.calls.map(([k]) => k);
    expect(keys.filter((k) => /^fa\.ds\..*publicLink$/.test(k))).toEqual([]);
    setItem.mockRestore();
  });
});
