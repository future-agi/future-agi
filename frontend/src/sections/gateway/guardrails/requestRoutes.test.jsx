import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import contract from "../../../../../api_contracts/gateway/guardrail-request-routes.json";
import {
  REQUEST_CHECK_NOT_SERVED,
  REQUEST_CHECK_ROUTES,
} from "../constants/guardrailRequestRoutes";
import RequestRoutesCard from "./RequestRoutesCard";

// The Guardrails page lists the routes whose request a Before LLM check
// reads, and what it reads there, exactly as
// api_contracts/gateway/guardrail-request-routes.json says. The gateway's
// test (guardrail_request_routes_contract_test.go) reads the same file and
// checks that every route it lists is one a request-check test probes, and
// that every route the gateway registers is in it, so the page cannot say a
// route is covered when the gateway does not check it.

describe("the routes Before LLM checks read match the gateway's", () => {
  it("the console's list is the contract's", () => {
    expect(REQUEST_CHECK_ROUTES).toEqual(
      contract.read.map(({ route, reads, unread, note }) => ({
        route,
        reads,
        ...(unread ? { unread } : {}),
        ...(note ? { note } : {}),
      })),
    );
    expect(REQUEST_CHECK_NOT_SERVED).toEqual(
      contract.not_served.map((r) => r.route),
    );
  });

  it("the card lists every route, with what is read and what is refused unread", () => {
    render(<RequestRoutesCard />);
    expect(
      screen.getByText(
        new RegExp(`read the request on ${contract.read.length} routes`),
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Routes Before LLM checks read" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show routes" }));
    const table = screen.getByRole("table", {
      name: "Routes Before LLM checks read",
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(contract.read.length);
    contract.read.forEach((r, i) => {
      const cells = within(rows[i]).getAllByRole("cell");
      expect(cells[0]).toHaveTextContent(r.route);
      expect(cells[1]).toHaveTextContent(r.reads);
      if (r.note) expect(cells[1]).toHaveTextContent(r.note);
      expect(cells[2]).toHaveTextContent(r.unread || "—");
    });
    for (const r of contract.not_served) {
      expect(screen.getByText(new RegExp(r.route))).toBeInTheDocument();
    }
  });

  it("a route the gateway refuses unread says so", () => {
    render(<RequestRoutesCard />);
    fireEvent.click(screen.getByRole("button", { name: "Show routes" }));
    const table = screen.getByRole("table", {
      name: "Routes Before LLM checks read",
    });
    const row = within(table)
      .getByText("POST /v1/audio/transcriptions")
      .closest("tr");
    expect(row).toHaveTextContent("The prompt");
    expect(row).toHaveTextContent("The audio file");
  });
});
