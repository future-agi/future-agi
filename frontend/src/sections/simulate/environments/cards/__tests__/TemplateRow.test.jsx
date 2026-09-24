import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import TemplateRow from "../TemplateRow";

const TEMPLATE = {
  id: "env-voice-support",
  name: "Customer Support Line",
  surface: "voice",
  tagline: "Inbound phone support for an online storefront",
  difficulty: "Starter",
  tools: [{ name: "lookup_order" }, { name: "issue_refund" }],
  rules: ["Refunds need approval"],
  seed: { tables: [{ name: "orders", rows: 500, note: "delayed" }] },
};

describe("TemplateRow", () => {
  it("renders the surface, name, tagline and stat line", () => {
    render(<TemplateRow template={TEMPLATE} />);

    // Surface is rendered lowercase, uppercased with CSS text-transform.
    expect(screen.getByText("voice")).toBeInTheDocument();
    expect(screen.getByText("Customer Support Line")).toBeInTheDocument();
    expect(
      screen.getByText("Inbound phone support for an online storefront"),
    ).toBeInTheDocument();
    // 2 tools*4 + 1 rule*3 + 1 trap*3 + Starter depth 3*2 = 20 scenarios.
    expect(
      screen.getByText("20 scenarios · 2 tools · 500 rows"),
    ).toBeInTheDocument();
  });

  it("shows the POPULAR marker only when popular", () => {
    const { rerender } = render(<TemplateRow template={TEMPLATE} />);
    expect(screen.queryByText("Popular")).not.toBeInTheDocument();

    rerender(<TemplateRow template={TEMPLATE} popular />);
    expect(screen.getByText("Popular")).toBeInTheDocument();
  });

  it("fires onClick on click and Enter", () => {
    const onClick = vi.fn();
    render(<TemplateRow template={TEMPLATE} onClick={onClick} />);

    const row = screen.getByRole("button");
    expect(row).toHaveAttribute("tabindex", "0");

    fireEvent.click(row);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onClick).toHaveBeenCalledTimes(2);
  });

  it("exposes aria-pressed when selected", () => {
    render(<TemplateRow template={TEMPLATE} selected onClick={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });
});
