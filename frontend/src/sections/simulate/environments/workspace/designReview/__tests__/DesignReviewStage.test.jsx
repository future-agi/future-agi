import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
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
  revision: "a".repeat(64),
};

const mutation = { mutate: vi.fn(), isPending: false };
const discard = { mutate: vi.fn(), isPending: false };
let current = review;

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));
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
  useDiscardDesign: () => discard,
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

  it("approves the revision it shows", () => {
    current = review;
    mutation.mutate.mockClear();
    render(<DesignReviewStage envId="e1" />);
    fireEvent.click(screen.getByRole("button", { name: "Approve and build" }));
    expect(mutation.mutate).toHaveBeenCalledWith(
      review.revision,
      expect.any(Object),
    );
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

describe("DesignReviewStage with a design that cannot be approved", () => {
  it.each([
    ["an empty design", { rules: [], use_cases: [] }],
    ["a design whose agent changed", { agent_changed: true }],
  ])("offers to discard %s and start again", (_label, change) => {
    current = { ...review, ...change };
    discard.mutate.mockClear();
    render(<DesignReviewStage envId="e1" />);
    expect(
      screen.getByText(/create the environment again/i),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard design" }));

    expect(discard.mutate).toHaveBeenCalledTimes(1);
  });

  it("offers no discard on a design that can be approved", () => {
    current = review;
    render(<DesignReviewStage envId="e1" />);
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });
});
