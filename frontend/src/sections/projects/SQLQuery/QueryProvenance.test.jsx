import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import QueryProvenance from "./QueryProvenance";

describe("SQL completion provenance", () => {
  it("displays RFC3339 offsets and microseconds as the same instant without changing the wire value", () => {
    const onRefresh = vi.fn();
    const utc = "2026-11-03T12:30:00.123456Z";
    const offset = "2026-11-03T18:00:00.123456+05:30";
    const { rerender } = render(
      <QueryProvenance finishedAt={utc} reused onRefresh={onRefresh} />,
    );
    const label = screen.getByText(/Reused result/).textContent;
    rerender(
      <QueryProvenance finishedAt={offset} reused onRefresh={onRefresh} />,
    );
    expect(screen.getByText(/Reused result/)).toHaveTextContent(label);
    expect(
      screen.getByText(/Reused result/).querySelector("time"),
    ).toHaveAttribute("datetime", offset);
    fireEvent.click(screen.getByRole("button", { name: "Run fresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    rerender(
      <QueryProvenance
        finishedAt={offset}
        reused
        onRefresh={onRefresh}
        disabled
      />,
    );
    expect(screen.getByRole("button", { name: "Run fresh" })).toBeDisabled();
  });
  it.each([null, undefined, "invalid"])(
    "does not invent a completion time for %s",
    (finishedAt) => {
      const { container } = render(
        <QueryProvenance finishedAt={finishedAt} onRefresh={vi.fn()} />,
      );
      expect(container).toBeEmptyDOMElement();
    },
  );
});
