import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { buildEnvironmentColumns } from "../components/environmentTableColumns";

const updatedCell = (value) => {
  const column = buildEnvironmentColumns({ onRowActions: vi.fn() }).find(
    (col) => col.id === "updated",
  );
  return column.cell({ getValue: () => value });
};

describe("the Updated column", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-15T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders date-fns's strict phrasing, the same as the rest of the product", () => {
    render(updatedCell("2026-09-15T09:00:00Z"));
    expect(screen.getByText("3 hours ago")).toBeInTheDocument();
  });

  it("falls back to a dash when the harness sends no timestamp", () => {
    render(updatedCell(null));
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("falls back to a dash when the timestamp cannot be parsed", () => {
    render(updatedCell("not-a-date"));
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
