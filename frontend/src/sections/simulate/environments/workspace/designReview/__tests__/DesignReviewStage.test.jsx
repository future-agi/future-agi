import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DesignReviewStage from "../DesignReviewStage";
import { REVIEW_STATUS } from "../designReview.constants";

const review = {
  status: REVIEW_STATUS.AWAITING,
  created_at: new Date().toISOString(),
  tables: [],
  tools: [],
  rules: [{ id: "r1", text: "Greet the caller." }],
  use_cases: [{ id: "u1", text: "Check an order.", planned_scenarios: 2 }],
  actors: [],
  unsupported: [],
  brief: {},
};

const mutation = { mutate: vi.fn(), isPending: false };
let current = review;

vi.mock("src/api/simulate-environments/designReview", () => ({
  getDesignImpact: vi.fn(),
  useDesignReview: () => ({
    data: current,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useAddDesignEdit: () => mutation,
  useUndoDesignEdit: () => mutation,
  useApproveDesign: () => mutation,
}));

vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit: true, canRun: true }),
}));

describe("DesignReviewStage with no tools or records", () => {
  it("lets an agent with rules and use cases be approved", () => {
    current = review;
    render(<DesignReviewStage envId="e1" />);
    expect(screen.queryByText(/nothing to build yet/i)).toBeNull();
    expect(screen.getByText("Check an order.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve and build" }),
    ).toBeEnabled();
  });

  it("blocks approval when the design has nothing in it", () => {
    current = { ...review, rules: [], use_cases: [] };
    render(<DesignReviewStage envId="e1" />);
    expect(screen.getByText(/nothing to build yet/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve and build" }),
    ).toBeDisabled();
  });
});
