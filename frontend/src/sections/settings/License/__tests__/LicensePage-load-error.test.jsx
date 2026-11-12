import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, fireEvent } from "src/utils/test-utils";

import LicensePage from "../LicensePage";

// W10-SETTINGS-TASKS-U03: a failed read of the license is not a dead end:
// the page says what to do and offers a Retry.

const h = vi.hoisted(() => ({ state: {} }));

vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useCapabilities: () => h.state,
}));

beforeEach(() => {
  h.state = {
    data: undefined,
    isLoading: false,
    isError: true,
    isRefetching: false,
    refetch: vi.fn(),
  };
});

describe("License page load failure", () => {
  it("offers Retry", () => {
    render(<LicensePage />);

    const failure = within(screen.getByRole("alert"));
    expect(
      failure.getByText("Failed to load license information"),
    ).toBeVisible();
    expect(failure.getByText("Check your connection and retry.")).toBeVisible();

    const retry = failure.getByRole("button", { name: "Retry" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    expect(h.state.refetch).toHaveBeenCalledTimes(1);
  });

  it("disables Retry while the license is read again", () => {
    h.state.isRefetching = true;
    render(<LicensePage />);

    expect(
      within(screen.getByRole("alert")).getByRole("button", {
        name: "Retrying…",
      }),
    ).toBeDisabled();
  });
});
