import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  WidgetEditorLoadFailure,
  WidgetPreviewStatus,
} from "../widgetEditorStatus";
import {
  getWidgetEditorLoadState,
  getWidgetPreviewState,
  shouldBlockWidgetPreviewForFailure,
  WIDGET_PREVIEW_MAX_WAIT_MS,
} from "../widgetEditorState";

describe("shouldBlockWidgetPreviewForFailure", () => {
  it("keeps an existing exact preview visible when refresh fails", () => {
    expect(
      shouldBlockWidgetPreviewForFailure({
        previewFailed: true,
        hasExactPreview: true,
      }),
    ).toBe(false);
  });

  it("blocks rendering when refresh fails without an exact preview", () => {
    expect(
      shouldBlockWidgetPreviewForFailure({
        previewFailed: true,
        hasExactPreview: false,
      }),
    ).toBe(true);
  });
});

describe("widget editor terminal load states", () => {
  it("keeps the editor loading only while dashboard detail is in flight", () => {
    expect(getWidgetEditorLoadState({ isEditing: true, isLoading: true })).toBe(
      "loading",
    );
  });

  it("returns an explicit error when dashboard detail fails", () => {
    expect(
      getWidgetEditorLoadState({
        isEditing: true,
        isLoading: false,
        isError: true,
      }),
    ).toBe("error");
  });

  it("also blocks a new-widget editor when its dashboard cannot load", () => {
    expect(
      getWidgetEditorLoadState({
        isEditing: false,
        isLoading: false,
        isError: true,
      }),
    ).toBe("error");
  });

  it("returns an explicit missing state when detail omits the widget", () => {
    expect(
      getWidgetEditorLoadState({
        isEditing: true,
        isLoading: false,
        isError: false,
        dashboard: { widgets: [{ id: "another-widget" }] },
        widgetId: "target-widget",
      }),
    ).toBe("missing");
  });

  it("renders retry/back actions for detail errors and a back action for missing widgets", () => {
    const retry = vi.fn();
    const back = vi.fn();
    const { rerender } = render(
      <WidgetEditorLoadFailure kind="error" onRetry={retry} onBack={back} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    fireEvent.click(screen.getByRole("button", { name: "Back to dashboard" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(back).toHaveBeenCalledOnce();

    rerender(<WidgetEditorLoadFailure kind="missing" onBack={back} />);
    expect(
      screen.getByText("This widget is no longer available."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

describe("exact widget preview states", () => {
  it("allows a slow exact preview to use the reviewed analytics wall", () => {
    expect(WIDGET_PREVIEW_MAX_WAIT_MS).toBe(220_000);
  });

  it.each([
    [{ query_status: "pending", metrics: [] }, "preparing"],
    [{ query_refreshing: true, metrics: [] }, "preparing"],
    [{ queryStatus: "pending", metrics: [] }, "preparing"],
    [{}, "preparing"],
    [{ metrics: [] }, "preparing"],
    [{ query_complete: true, query_status: "complete", metrics: [] }, "ready"],
    [{ query_refresh_failed: true, metrics: [] }, "failed"],
    [{ queryRefreshFailed: true, metrics: [] }, "failed"],
    [{ query_complete: false, query_status: "degraded" }, "failed"],
  ])("maps %j to %s", (result, expected) => {
    expect(getWidgetPreviewState(result, { isSuccess: true })).toBe(expected);
  });

  it("renders a visible preparing state instead of a blank spinner", () => {
    render(<WidgetPreviewStatus state="preparing" />);
    expect(screen.getByRole("status")).toHaveTextContent("Preparing data");
  });

  it("renders a sanitized failure with a working retry", () => {
    const retry = vi.fn();
    render(<WidgetPreviewStatus state="failed" onRetry={retry} />);
    expect(
      screen.getByText(
        "Data could not be prepared. Try again or narrow the time range.",
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
