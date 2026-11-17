import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import PromptsTab from "./PromptsTab";

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});
const { query } = vi.hoisted(() => ({ query: { current: {} } }));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPPrompts: () => query.current,
}));

describe("PromptsTab", () => {
  it("lists prompts with their arguments, required ones marked", () => {
    query.current = {
      data: [
        {
          name: "summarize_ticket",
          description: "Summarize a ticket thread",
          arguments: [{ name: "ticket_id", required: true }, { name: "tone" }],
          server_id: "support-tools",
        },
      ],
      isLoading: false,
    };
    renderMcp(<PromptsTab gatewayId="gw-1" />);
    const [row] = screen.getAllByRole("row");
    expect(within(row).getByText("summarize_ticket")).toBeInTheDocument();
    expect(within(row).getByText("ticket_id*, tone")).toBeInTheDocument();
    fireEvent.click(row);
    expect(
      screen.getByRole("dialog", { name: "summarize_ticket" }),
    ).toBeInTheDocument();
  });

  it("finds a prompt by its description and shows each argument's details", () => {
    query.current = {
      data: [
        {
          name: "summarize_ticket",
          description: "Summarize a ticket thread",
          arguments: [
            {
              name: "ticket_id",
              required: true,
              description: "The ticket to read",
            },
            { name: "tone" },
          ],
          server_id: "support-tools",
        },
        {
          name: "draft_reply",
          description: "Draft a reply",
          server_id: "support-tools",
        },
      ],
      isLoading: false,
    };
    renderMcp(<PromptsTab gatewayId="gw-1" />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search prompts" }), {
      target: { value: "ticket thread" },
    });
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(1);
    fireEvent.click(rows[0]);
    const drawer = screen.getByRole("dialog", { name: "summarize_ticket" });
    expect(
      within(drawer).getByText("Summarize a ticket thread"),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("ticket_id")).toBeInTheDocument();
    expect(within(drawer).getByText("Required")).toBeInTheDocument();
    expect(within(drawer).getByText("The ticket to read")).toBeInTheDocument();
    expect(within(drawer).getByText("tone")).toBeInTheDocument();
  });

  it("explains an empty list", () => {
    query.current = { data: [], isLoading: false };
    renderMcp(<PromptsTab gatewayId="gw-1" />);
    expect(screen.getByText("No prompts")).toBeInTheDocument();
  });
});
