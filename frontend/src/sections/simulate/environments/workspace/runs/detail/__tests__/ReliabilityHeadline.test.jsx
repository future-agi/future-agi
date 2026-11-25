import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

const useRunReliability = vi.fn();
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunReliability: (...args) => useRunReliability(...args),
}));

const { default: ReliabilityHeadline } = await import(
  "../ReliabilityHeadline"
);

describe("ReliabilityHeadline", () => {
  const reliability = {
    k: 2,
    pass_hat_1: 100,
    pass_hat_k: 100,
    pass_hat_k_scenarios: 1,
    pass_hat_k_passed: 1,
    pass_hat_k_excluded: 0,
    trials_passed: 2,
    trials_evaluated: 2,
    inconclusive: 0,
    excluded_not_comparable: 0,
    pass_rate_interval: null,
    rows: [{ evaluated: 2 }],
  };
  const serve = (fields) =>
    useRunReliability.mockReturnValue({ data: { ...reliability, ...fields } });

  beforeEach(() => useRunReliability.mockReset());

  it("shows the served count of trials left out for not starting clean", () => {
    serve({ excluded_not_comparable: 1 });
    render(<ReliabilityHeadline executionId="execution-1" enabled />);

    expect(screen.getByText("1 trial")).toBeInTheDocument();
  });
});
