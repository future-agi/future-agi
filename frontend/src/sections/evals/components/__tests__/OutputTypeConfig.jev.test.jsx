import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import OutputTypeConfig from "../OutputTypeConfig";
import FewShotExamples from "../FewShotExamples";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async () => ({
      data: { result: { datasets: [{ id: "examples", name: "Examples" }] } },
    })),
  },
  endpoints: { develop: { getDatasets: () => "/datasets" } },
}));

const props = {
  outputType: "pass_fail",
  isJevModel: true,
  passThreshold: 0.7,
  choiceScores: {},
  onOutputTypeChange: vi.fn(),
  onChoiceScoresChange: vi.fn(),
  onPassThresholdChange: vi.fn(),
  onMultiChoiceChange: vi.fn(),
};

beforeEach(() => vi.clearAllMocks());

describe("Jev output controls", () => {
  it.each(["pass_fail", "percentage"])(
    "shows the %s pass threshold at rest and updates the visible percentage",
    (outputType) => {
      const view = render(
        <OutputTypeConfig {...props} outputType={outputType} />,
      );
      const label = screen.getByText("Pass threshold");
      expect(label).toHaveTextContent("70%");
      fireEvent.change(screen.getByRole("slider", { name: "Pass threshold" }), {
        target: { value: "85" },
      });
      expect(props.onPassThresholdChange).toHaveBeenCalledWith(0.85);
      view.rerender(
        <OutputTypeConfig
          {...props}
          outputType={outputType}
          passThreshold={0.85}
        />,
      );
      expect(label).toHaveTextContent("85%");
    },
  );

  it("disables multi-choice for Jev without changing a saved value", () => {
    const view = render(
      <OutputTypeConfig {...props} outputType="deterministic" multiChoice />,
    );
    const checkbox = screen.getByRole("checkbox", {
      name: /Allow multiple choices/,
    });
    expect(checkbox).toBeDisabled();
    expect(checkbox).toBeChecked();
    expect(screen.getByText(/Jev selects one choice only/)).toBeInTheDocument();
    expect(props.onMultiChoiceChange).not.toHaveBeenCalled();
    view.rerender(
      <OutputTypeConfig
        {...props}
        outputType="deterministic"
        multiChoice
        isJevModel={false}
      />,
    );
    expect(checkbox).toBeEnabled();
    expect(checkbox).toBeChecked();
  });
});

it("disables few-shot datasets for Jev and preserves them when switching away", async () => {
  const onChange = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const field = (isJevModel) => (
    <QueryClientProvider client={qc}>
      <FewShotExamples
        selectedDatasets={["examples"]}
        onChange={onChange}
        isJevModel={isJevModel}
      />
    </QueryClientProvider>
  );
  const view = render(field(true));
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(await screen.findByText("Examples")).toBeInTheDocument();
  expect(
    screen.getByText(/Jev does not support few-shot examples/),
  ).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  view.rerender(field(false));
  expect(screen.getByRole("combobox")).toBeEnabled();
  expect(screen.getByText("Examples")).toBeInTheDocument();
});
