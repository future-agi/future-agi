import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AttributeInventoryControls from "../AttributeInventoryControls";

let intersectionObservers;

class MockIntersectionObserver {
  constructor(callback, options) {
    this.callback = callback;
    this.options = options;
    intersectionObservers.push(this);
  }

  observe(target) {
    this.target = target;
  }

  disconnect() {}

  trigger(isIntersecting) {
    this.callback([
      {
        target: this.target,
        isIntersecting,
        intersectionRatio: isIntersecting ? 1 : 0,
      },
    ]);
  }
}

describe("AttributeInventoryControls", () => {
  beforeEach(() => {
    intersectionObservers = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

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

  it("loads one page per viewport-end gesture without a manual load button", async () => {
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
      intersectionObservers[0].trigger(true);
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: /load more|continue/i }),
    ).not.toBeInTheDocument();

    await act(async () => {
      intersectionObservers[0].trigger(true);
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    await act(async () => {
      intersectionObservers[0].trigger(false);
      intersectionObservers[0].trigger(true);
    });
    expect(onLoadMore).toHaveBeenCalledTimes(2);
  });

  it("keeps an independent retry accessible without auto-retrying the cursor", async () => {
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
      intersectionObservers[0].trigger(true);
      fireEvent.click(screen.getByRole("button", { name: "Retry properties" }));
    });

    expect(onLoadMore).not.toHaveBeenCalled();
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Load more attributes")).not.toBeInTheDocument();
  });

  it("exposes a failed next page as an explicit retry and deduplicates clicks", async () => {
    let resolvePage;
    const onLoadMore = vi.fn(
      () => new Promise((resolve) => (resolvePage = resolve)),
    );
    render(
      <AttributeInventoryControls
        showSearch={false}
        hasNextPage
        isFetchNextPageError
        onLoadMore={onLoadMore}
      />,
    );

    act(() => intersectionObservers[0].trigger(true));
    expect(onLoadMore).not.toHaveBeenCalled();

    const retry = screen.getByRole("button", {
      name: "Retry next property page",
    });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    await act(async () => resolvePage());
  });

  it("deduplicates the same cursor callback across simultaneous consumers", async () => {
    let resolvePage;
    const onLoadMore = vi.fn(
      () => new Promise((resolve) => (resolvePage = resolve)),
    );
    render(
      <>
        <AttributeInventoryControls
          showSearch={false}
          hasNextPage
          onLoadMore={onLoadMore}
        />
        <AttributeInventoryControls
          showSearch={false}
          hasNextPage
          onLoadMore={onLoadMore}
        />
      </>,
    );

    act(() => {
      intersectionObservers[0].trigger(true);
      intersectionObservers[1].trigger(true);
    });
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    await act(async () => resolvePage());
  });
});
