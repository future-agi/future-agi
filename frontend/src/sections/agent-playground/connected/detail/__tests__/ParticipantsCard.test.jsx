import { describe, it, expect } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ParticipantsCard from "../ParticipantsCard";
import { PARTICIPANTS_COPY as C } from "../participants.constants";

const ORDER_LOOKUP = {
  id: "order_lookup",
  name: "Order-lookup worker",
  tools: ["get_order", "update_order_status"],
  allowed_tools: ["get_order"],
  tools_shown: ["get_order"],
  binding: "separate",
};
const REFUND = {
  id: "refund",
  tools: ["issue_refund"],
  allowed_tools: [],
  tools_shown: ["issue_refund"],
  binding: "not_declared",
};

describe("ParticipantsCard", () => {
  it("lists each served worker with the tools it may use, read-only", () => {
    render(<ParticipantsCard participants={[ORDER_LOOKUP, REFUND]} />);
    const [lookup, refund] = screen.getAllByTestId("declared-worker");
    expect(lookup).toHaveTextContent(ORDER_LOOKUP.name);
    expect(within(lookup).getByText("get_order")).toBeInTheDocument();
    // Only the tools the API serves as shown.
    expect(within(lookup).queryByText("update_order_status")).toBeNull();
    // No name: its id.
    expect(refund).toHaveTextContent(REFUND.id);
    expect(within(refund).getByText("issue_refund")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(C.title(2))).toBeInTheDocument();
  });

  it("shows nothing for an agent with no declared workers", () => {
    render(<ParticipantsCard participants={[]} />);
    expect(screen.queryByTestId("declared-worker")).toBeNull();
    expect(screen.queryByText(C.note)).toBeNull();
  });
});
