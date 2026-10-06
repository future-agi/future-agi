import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import TemplateRow from "../TemplateRow";

const TEMPLATE = {
  id: "banking_support",
  name: "Banking — Card, Fraud & Account Support",
  surface: "voice",
  domain: "Fintech",
  tagline: "Inbound card and fraud support",
  scenarioCount: 12,
  tools: [{ name: "verify_identity" }, { name: "lock_card" }],
  rules: ["Never move money"],
};

describe("TemplateRow", () => {
  it("renders the surface, domain, name, tagline and the generated suite size", () => {
    render(<TemplateRow template={TEMPLATE} />);

    // Surface is rendered lowercase, uppercased with CSS text-transform.
    expect(screen.getByText("voice")).toBeInTheDocument();
    expect(screen.getByText("Fintech")).toBeInTheDocument();
    expect(
      screen.getByText("Banking — Card, Fraud & Account Support"),
    ).toBeInTheDocument();
    expect(screen.getByText("Inbound card and fraud support")).toBeInTheDocument();
    expect(screen.getByText("12 scenarios · 2 tools")).toBeInTheDocument();
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
