import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SQLQueryWidget from "./SQLQueryWidget";
import { sqlApi } from "./api";

const admission = vi.hoisted(() => ({ active: null }));
vi.mock("./api", () => ({
  errorMessage: (error) => error.message,
  sqlApi: {
    runSaved: vi.fn(async ({ saved_query_id, project_id }) => {
      if (admission.active)
        throw Object.assign(new Error("A query is already running"), {
          statusCode: 429,
        });
      admission.active = saved_query_id;
      return { id: `job-${saved_query_id}`, state: "queued", project_id };
    }),
    result: vi.fn(async (project_id, id) => {
      admission.active = null;
      return {
        id,
        state: "succeeded",
        project_id,
        created_at: "2026-10-21T00:00:00Z",
        result: {
          columns: [{ name: "answer", type: "String" }],
          rows: [[id === "job-first" ? "first answer" : "second answer"]],
          row_count: 1,
          truncated: false,
          elapsed_ms: 1,
        },
      };
    }),
    cancel: vi.fn(async (_, id) => {
      admission.active = null;
      return { id, state: "cancelled" };
    }),
  },
}));

const widget = (id) => ({
  id,
  query_config: { source: "sql", project_id: "project", saved_query_id: id },
  chart_config: { type: "table" },
});

describe("SQL dashboard admission backpressure", () => {
  it("eventually displays two distinct widget results without weakening one-active-query admission", async () => {
    admission.active = null;
    render(
      <ThemeProvider theme={createTheme()}>
        <section aria-label="first widget">
          <SQLQueryWidget widget={widget("first")} />
        </section>
        <section aria-label="second widget">
          <SQLQueryWidget widget={widget("second")} />
        </section>
      </ThemeProvider>,
    );
    await screen.findByText("Waiting for a query slot…");
    await waitFor(
      () => {
        expect(
          within(
            screen.getByRole("region", { name: "first widget" }),
          ).getByText("first answer"),
        ).toBeInTheDocument();
        expect(
          within(
            screen.getByRole("region", { name: "second widget" }),
          ).getByText("second answer"),
        ).toBeInTheDocument();
      },
      { timeout: 5000 },
    );
    expect(
      sqlApi.runSaved.mock.calls.filter(
        ([body]) => body.saved_query_id === "second",
      ).length,
    ).toBeGreaterThan(1);
    expect(new Set(sqlApi.result.mock.calls.map(([, id]) => id))).toEqual(
      new Set(["job-first", "job-second"]),
    );
    expect(
      within(screen.getByRole("region", { name: "first widget" })).queryByText(
        "second answer",
      ),
    ).toBeNull();
  });
});
