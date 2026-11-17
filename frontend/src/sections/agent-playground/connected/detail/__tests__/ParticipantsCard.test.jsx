import { describe, it, expect } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ParticipantsCard from "../ParticipantsCard";
import { PARTICIPANTS_COPY as C } from "../participants.constants";

const ORDER_LOOKUP = {
  id: "order_lookup",
  name: "Order-lookup worker",
  display_name: "Order-lookup worker",
  tools: ["get_order", "update_order_status"],
  allowed_tools: ["get_order"],
  tools_shown: ["get_order"],
  binding: "separate",
  enforcement: "held_at_world",
  enforcement_label: "Held at the world: its own channel",
};
const REFUND = {
  id: "refund",
  // No name: the server names it by its id.
  display_name: "refund",
  tools: ["issue_refund"],
  allowed_tools: [],
  tools_shown: ["issue_refund"],
  binding: "not_declared",
  enforcement: "not_enforced",
  enforcement_label: "Not enforced: it shares the app's address",
};

describe("ParticipantsCard", () => {
  it("lists each served worker with the tools it may use, read-only", () => {
    render(<ParticipantsCard participants={[ORDER_LOOKUP, REFUND]} />);
    const [lookup, refund] = screen.getAllByTestId("declared-worker");
    expect(lookup).toHaveTextContent(ORDER_LOOKUP.display_name);
    expect(within(lookup).getByText("get_order")).toBeInTheDocument();
    // Only the tools the API serves as shown.
    expect(within(lookup).queryByText("update_order_status")).toBeNull();
    expect(refund).toHaveTextContent(REFUND.display_name);
    expect(within(refund).getByText("issue_refund")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(C.title(2))).toBeInTheDocument();
  });

  it("says how the world holds each worker, in the server's words", () => {
    render(<ParticipantsCard participants={[ORDER_LOOKUP, REFUND]} />);
    const shown = screen
      .getAllByTestId("worker-enforcement")
      .map((one) => one.textContent);
    expect(shown).toEqual([
      ORDER_LOOKUP.enforcement_label,
      REFUND.enforcement_label,
    ]);
  });

  it("shows nothing for an agent with no declared workers", () => {
    render(<ParticipantsCard participants={[]} />);
    expect(screen.queryByTestId("declared-worker")).toBeNull();
    expect(screen.queryByText(C.note)).toBeNull();
  });
});
