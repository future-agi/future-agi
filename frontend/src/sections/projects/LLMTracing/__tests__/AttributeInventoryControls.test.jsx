import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AttributeInventoryControls from "../AttributeInventoryControls";

describe("AttributeInventoryControls", () => {
  it("shows a sanitized initial error and runs one retry per gesture", async () => {
    let resolveRetry;
    const onRetry = vi.fn(
      () => new Promise((resolve) => (resolveRetry = resolve)),
    );

    render(
      <AttributeInventoryControls
        showSearch={false}
        isError
        canRetry
        onRetry={onRetry}
      />,
    );

    expect(
      screen.getByText("Properties could not be loaded. Retry this page."),
    ).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry properties" });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);

    await act(async () => resolveRetry());
  });

  it("keeps an exhausted cursor visible without creating a retry loop", () => {
    render(
      <AttributeInventoryControls showSearch={false} cursorRetryExhausted />,
    );

    expect(
      screen.getByText(
        "Attribute pagination stopped safely. Loaded properties remain available.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("demotes a failed exact continuation to retained pagination", async () => {
    const onLoadMore = vi.fn(() => Promise.resolve());
    render(
      <AttributeInventoryControls
        showSearch={false}
        hasNextPage
        isExactSearchDegraded
        onLoadMore={onLoadMore}
      />,
    );

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Continue retained properties" }),
      );
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("keeps pagination available alongside an independent retry", async () => {
    const onRetry = vi.fn(() => Promise.resolve());
    const onLoadMore = vi.fn(() => Promise.resolve());
    render(
      <AttributeInventoryControls
        showSearch={false}
        hasNextPage
        onLoadMore={onLoadMore}
        isError
        canRetry
        onRetry={onRetry}
      />,
    );

    expect(
      screen.getByText("Properties could not be loaded. Retry this page."),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Load more attributes" }),
      );
    });

    expect(onLoadMore).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });
});
