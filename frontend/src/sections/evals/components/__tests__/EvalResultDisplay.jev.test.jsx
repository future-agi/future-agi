import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import EvalResultDisplay from "../EvalResultDisplay";

vi.mock("@monaco-editor/react", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/ErrorLocalizeCard", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-audio/AudioErrorCard", () => ({
  default: () => null,
}));
vi.mock("src/components/inline-audio/inline-row-audio", () => ({
  InlineAudio: () => null,
}));
vi.mock("../CompositeResultView", () => ({ default: () => null }));

beforeEach(() =>
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  ),
);
afterEach(() => vi.unstubAllGlobals());
const base = {
  requested_model: "jev-latest",
  actual_model: "jev-1.13.0",
  mapping_revision: "jev-map-v1:abc123",
  usage: { input_tokens: 412, output_tokens: 6 },
};
function result(jev, output, extra = {}) {
  return {
    output,
    reason: null,
    metadata: { jev: { ...base, ...jev } },
    ...extra,
  };
}

describe("Jev result display", () => {
  it("renders the verdict and raw probability without inventing confidence or reasoning", () => {
    const value = result(
      {
        question_type: "noul",
        verdict: "pass",
        probability: 0.91,
        threshold: 0.5,
      },
      "Passed",
    );
    render(<EvalResultDisplay result={value} />);
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Probability: 0.91")).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: "Probability" }),
    ).toHaveAttribute("aria-valuenow", "91");
    expect(screen.getByText("Requested: jev-latest")).toBeInTheDocument();
    expect(screen.getByText("Returned: jev-1.13.0")).toBeInTheDocument();
    expect(screen.getByText("Mapping: jev-map-v1:abc123")).toBeInTheDocument();
    expect(
      screen.getByText("Usage: 412 input / 6 output tokens"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Jev does not provide written reasoning."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Confidence/)).not.toBeInTheDocument();
    expect(screen.queryByText("Explanation")).not.toBeInTheDocument();
    expect(value.reason).toBeNull();
  });

  it("keeps zero probability and a valid failed verdict distinct from execution failure", () => {
    render(
      <EvalResultDisplay
        result={result(
          {
            question_type: "noul",
            verdict: "fail",
            probability: 0,
            threshold: 0.5,
          },
          "Failed",
          { failure: true },
        )}
      />,
    );
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByText("Probability: 0.00")).toBeInTheDocument();
    expect(
      screen.queryByText("No Turing fallback was applied"),
    ).not.toBeInTheDocument();
  });

  it("renders exact choice labels, the full distribution, confidence and choice score", () => {
    render(
      <EvalResultDisplay
        result={result(
          {
            question_type: "choice",
            distribution: { "Oui ✓": 0.8, "Non!": 0.2 },
            confidence: 0.6,
          },
          { choice: "Oui ✓", score: 0 },
        )}
      />,
    );
    expect(screen.getByText("Chosen label: Oui ✓")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Oui ✓" })).toHaveAttribute(
      "aria-valuenow",
      "80",
    );
    expect(screen.getByRole("progressbar", { name: "Non!" })).toHaveAttribute(
      "aria-valuenow",
      "20",
    );
    expect(
      screen.getByText("Confidence: 0.60 (provider-reported)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Score: 0.00")).toBeInTheDocument();
  });

  it("renders fractional expected level, normalized score, legend, and confidence from serialized metadata", () => {
    const value = result(
      {
        question_type: "score",
        raw_score: 2.1,
        normalized_score: 0.7,
        legend: {
          0: "Missing",
          1: "Major errors",
          2: "Mostly correct",
          3: "Complete",
        },
        distribution: { 0: 0.02, 1: 0.1, 2: 0.66, 3: 0.22 },
        confidence: 0.66,
      },
      0.7,
    );
    value.metadata = JSON.stringify(value.metadata);
    render(<EvalResultDisplay result={value} />);
    expect(screen.getByText("Expected level: 2.10")).toBeInTheDocument();
    expect(screen.getByText("Normalized score: 0.70")).toBeInTheDocument();
    expect(screen.getByText("2 — Mostly correct")).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: "2 — Mostly correct" }),
    ).toHaveAttribute("aria-valuenow", "66");
  });

  it.each([
    "Jev request timed out",
    { message: "Jev response failed validation", code: "jev_invalid_response" },
  ])("shows failures without a score or Turing fallback (%j)", (error) => {
    render(
      <EvalResultDisplay
        result={result(
          { question_type: "score", actual_model: null, normalized_score: 0 },
          0,
          { error },
        )}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      typeof error === "string" ? error : error.message,
    );
    expect(
      screen.getByText("No Turing fallback was applied"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Normalized score:/)).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("Returned: —")).toBeInTheDocument();
  });

  it("renders a Jev failure even without a legacy output or reason", () => {
    render(
      <EvalResultDisplay
        result={result({ actual_model: null }, undefined, {
          error: "Entitlement denied",
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Entitlement denied");
  });

  it("keeps legacy results unchanged including their explanation", () => {
    const { container } = render(
      <EvalResultDisplay
        result={{
          output: "Passed",
          output_type: "Pass/Fail",
          reason: "Grounded in evidence",
        }}
      />,
    );
    expect(screen.getByText("Explanation")).toBeInTheDocument();
    expect(screen.getByText("Grounded in evidence")).toBeInTheDocument();
    expect(within(container).queryByText(/Jev/)).not.toBeInTheDocument();
    expect(screen.getByText("Formatted")).toBeInTheDocument();
  });
});
