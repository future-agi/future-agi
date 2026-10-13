import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SourceNavigationRow from "../SourceNavigationRow";

const states = [
  ["loading", "Checking source...", null],
  ["trace", "Source:", "View trace"],
  ["voice_call", "Source:", "View call"],
  [
    "no_reference",
    "No trace or call reference was recorded for this log.",
    null,
  ],
  [
    "unsupported_source",
    "Trace or call navigation is not available for this log source.",
    null,
  ],
  [
    "unsupported_target",
    "This evaluation targets a session. Trace or call navigation is not available here.",
    null,
  ],
  [
    "incomplete_reference",
    "This log does not contain enough source information to open a trace or call.",
    null,
  ],
  ["invalid_reference", "This log's source reference is invalid.", null],
  [
    "ambiguous_reference",
    "This log's source cannot be identified uniquely.",
    null,
  ],
  ["unavailable", "The source is unavailable or you do not have access.", null],
  [
    "temporarily_unavailable",
    "Source details could not be loaded. Try again.",
    "Retry",
  ],
  [
    "unsupported_backend",
    "Source navigation is not available on this server.",
    null,
  ],
];

describe("source row", () => {
  it.each(states)(
    "R01 renders exact copy and action count for %s",
    (state, copy, button) => {
      const ready = ["trace", "voice_call"].includes(state);
      render(
        <SourceNavigationRow
          isPending={state === "loading"}
          nav={{ status: ready ? "ready" : state, kind: ready ? state : null }}
        />,
      );
      expect(screen.getByText(copy, { exact: true })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent(copy);
      expect(screen.queryAllByRole("button")).toHaveLength(button ? 1 : 0);
      if (button)
        expect(
          screen.getByRole("button", { name: button, exact: true }),
        ).toHaveClass("MuiButton-root");
    },
  );

  it("R02 supports Enter and Space activation with the exact accessible name", async () => {
    const user = userEvent.setup();
    const onActivate = vi.fn();
    render(
      <SourceNavigationRow
        nav={{ status: "ready", kind: "trace" }}
        onActivate={onActivate}
      />,
    );
    await user.tab();
    expect(
      screen.getByRole("button", { name: "View trace", exact: true }),
    ).toHaveFocus();
    await user.keyboard("{Enter} ");
    expect(onActivate).toHaveBeenCalledTimes(2);
  });

  it("R03 announces pending state without moving focus", () => {
    render(
      <>
        <button>Existing control</button>
        <SourceNavigationRow isPending />
      </>,
    );
    screen.getByRole("button").focus();
    expect(screen.getByRole("status")).toHaveTextContent("Checking source...");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("button")).toHaveFocus();
  });

  it("R04 retries once per click and honors the in-flight disabled state", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const props = { nav: { status: "temporarily_unavailable" }, onRetry };
    const { rerender } = render(<SourceNavigationRow {...props} />);
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
    rerender(<SourceNavigationRow {...props} disabled />);
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  });

  it.each([400, 429, 500, 503, undefined])(
    "R01 maps transport status %s to retryable even with stale ready data",
    (statusCode) => {
      render(
        <SourceNavigationRow
          nav={{ status: "ready", kind: "trace" }}
          isError
          error={{ statusCode }}
        />,
      );
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "View trace" }),
      ).not.toBeInTheDocument();
    },
  );

  it("R01 maps forbidden to unavailable and clears expired-session content", () => {
    const { rerender } = render(
      <SourceNavigationRow isError error={{ statusCode: 403 }} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "The source is unavailable or you do not have access.",
    );
    rerender(<SourceNavigationRow isError error={{ statusCode: 401 }} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
