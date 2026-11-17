// @vitest-environment jsdom
/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import useColumnLayout from "../useColumnLayout";
import ViewMenu from "../ViewMenu";

// IT-R1 integration: a caller's `groups` reach the Columns panel opened from
// View > Columns · Edit… (Results passes RESULTS_COLUMN_GROUPS), so the panel
// shows `Row fields` / `Per run`, not the raw ids `example` / `perRun`.
const COLS = [
  { id: "input", label: "Input", group: "example", locked: true },
  { id: "expected", label: "Expected", group: "example" },
  { id: "run-a", label: "#2A output", group: "perRun" },
];
const GROUPS = [
  { id: "example", label: "Row fields" },
  { id: "perRun", label: "Per run" },
];

function Page({ groups }) {
  const layout = useColumnLayout({
    surface: "results",
    scopeId: groups ? "with" : "without",
    columns: COLS,
    commands: false,
  });
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ViewMenu
          surface="results"
          columns={layout}
          groups={groups}
          sections={false}
          iconOnly={false}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function openColumns() {
  fireEvent.click(screen.getByRole("button", { name: /view/i }));
  fireEvent.click(screen.getByRole("button", { name: "Edit…" }));
}

describe("ViewMenu groups", () => {
  it("forwards groups to the Columns panel", () => {
    render(<Page groups={GROUPS} />);
    openColumns();
    expect(screen.getByText("Row fields")).toBeTruthy();
    expect(screen.getByText("Per run")).toBeTruthy();
    expect(screen.queryByText("perRun")).toBeNull();
  });

  it("without groups the panel falls back to the raw group ids", () => {
    render(<Page />);
    openColumns();
    expect(screen.getByText("perRun")).toBeTruthy();
  });
});
