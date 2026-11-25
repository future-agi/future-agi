import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SQLQueryWidget from "./SQLQueryWidget";
import useSQLExecution from "./useSQLExecution";

vi.mock("./useSQLExecution", () => ({ default: vi.fn() }));

// Real QueryResults: a grid mock would hide the inappropriate editor guidance.
describe("SQL widget pending copy", () => {
  it.each([
    ["submitting", "Query submitting"],
    ["queued", "Query queued"],
    ["running", "Query running"],
    ["admission", "Waiting for a query slot"],
    ["cancelled", "Query cancelled"],
  ])("agrees on caption and body for %s", (state, label) => {
    useSQLExecution.mockReturnValue({
      job: ["submitting", "admission"].includes(state)
        ? null
        : { id: "widget-job", state },
      busy: state !== "cancelled",
      waitingForAdmission: state === "admission",
      error: "",
      run: vi.fn(),
      cancel: vi.fn(),
    });
    render(
      <ThemeProvider theme={createTheme()}>
        <SQLQueryWidget
          widget={{
            id: "widget",
            query_config: { project_id: "project", saved_query_id: "saved" },
            chart_config: { type: "table" },
          }}
        />
      </ThemeProvider>,
    );
    const caption = screen.getByRole("status");
    expect(caption.textContent.replace(/…$/, "")).toBe(label);
    expect(
      screen.getByText(label, { exact: true, selector: ":not([role=status])" }),
    ).toBeVisible();
  });
  it.each(["submitting", "queued", "running", "admission", "cancelled"])(
    "uses neutral result copy for %s, retaining saved-query routing and Cancel",
    (state) => {
      const run = vi.fn();
      const cancel = vi.fn();
      const busy = state !== "cancelled";
      useSQLExecution.mockReturnValue({
        job: ["submitting", "admission"].includes(state)
          ? null
          : { id: "widget-job", state },
        busy,
        waitingForAdmission: state === "admission",
        error: "",
        run,
        cancel,
      });
      render(
        <ThemeProvider theme={createTheme()}>
          <SQLQueryWidget
            widget={{
              id: "widget",
              query_config: { project_id: "project", saved_query_id: "saved" },
              chart_config: { type: "table" },
            }}
          />
        </ThemeProvider>,
      );
      expect(screen.queryByText(/Explore fields in Catalog/)).toBeNull();
      expect(screen.queryByText(/Run a query to inspect/)).toBeNull();
      if (state === "cancelled") {
        expect(
          screen.getByText("Query cancelled", {
            exact: true,
            selector: ":not([role=status])",
          }),
        ).toBeVisible();
        expect(
          screen.getByText("Run the query again when ready."),
        ).toBeVisible();
        expect(
          screen.queryByText(/Waiting for query results|Results will appear/),
        ).toBeNull();
      } else {
        expect(screen.getByText(/Results will appear/)).toBeVisible();
      }
      expect(run).toHaveBeenCalledWith({ saved_query_id: "saved" }, true);
      if (busy) {
        fireEvent.click(screen.getByRole("button", { name: "Cancel query" }));
        expect(cancel).toHaveBeenCalledTimes(1);
      }
      if (state === "admission") {
        expect(screen.getByRole("status")).toHaveTextContent(
          "Waiting for a query slot",
        );
      }
    },
  );
});
