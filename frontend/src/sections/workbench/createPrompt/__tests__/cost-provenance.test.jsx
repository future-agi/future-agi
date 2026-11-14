import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import RenderMeta from "src/sections/common/DevelopCellRenderer/RenderMeta";
import OutputMeta from "../Playground/OutputSection/OutputMeta";

afterEach(cleanup);

describe("Unit: model cost provenance", () => {
  it.each(["default", "fallback"])(
    "labels %s in workbench output",
    (source) => {
      render(
        <OutputMeta metadata={{ cost: 0.00045, pricingSource: source }} />,
      );
      expect(screen.getByText("0.00045 (estimated)")).toBeInTheDocument();
    },
  );

  it("shows a real zero rather than a positive-cost range", () => {
    render(
      <OutputMeta
        metadata={{
          cost: 0,
          pricingSource: "available_models",
          responseTime: 1,
        }}
      />,
    );
    expect(screen.getAllByText("0")).toHaveLength(2);
    expect(screen.queryByText("<0.1")).not.toBeInTheDocument();
  });

  it.each(["default", "fallback"])(
    "labels %s in dataset metadata",
    (source) => {
      render(
        <RenderMeta
          originType="run_prompt"
          meta={{ tokenCount: 1000 }}
          valuesInfo={{
            metadata: { cost: { total_cost: 0.00045, pricing_source: source } },
          }}
        />,
      );
      expect(screen.getByText("0.00045 (estimated)")).toBeInTheDocument();
    },
  );

  it("does not label missing cost as zero", () => {
    render(<OutputMeta metadata={{ responseTime: 1 }} />);
    expect(screen.getByText("-")).toBeInTheDocument();
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });
});
