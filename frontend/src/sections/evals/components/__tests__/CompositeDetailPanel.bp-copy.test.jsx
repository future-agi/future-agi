import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CompositeDetailPanel from "../CompositeDetailPanel";
import { bpRendered, bpText } from "./pinCopyBp.fixture";

vi.mock("src/sections/common/EvalPicker/EvalPickerDrawer", () => ({
  default: () => null,
}));
vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenSchemas: () => ({}),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("../../types/CompositeChildControls", () => ({ default: () => null }));
vi.mock("../../types/CompositeJuryReport", () => ({ default: () => null }));
vi.mock("../../types/CompositeAddCheck", () => ({ default: () => null }));
vi.mock("../../types/CompositeIssues", () => ({ default: () => null }));
vi.mock("../../jury/JuryPanel", () => ({ default: () => null }));

const mountChild = (eval_type, pinned_version_number) => {
  const child = {
    child_id: "child-bp",
    child_name: "Pinned child",
    eval_type,
    order: 0,
    pinned_version_id: "version-3",
    pinned_version_number,
    // Code children can retain a saved model while executing current code.
    config: { model: "turing_large" },
  };
  render(
    <CompositeDetailPanel
      name="Composite"
      aggregationEnabled={false}
      children={[child]}
    />,
  );
  return child;
};

describe("BP composite child copy", () => {
  it.each(["code", "llm"])(
    "A3-01/02 describe a pinned %s child without claiming model execution",
    (evalType) => {
      const child = mountChild(evalType, 3);
      const caption = screen.getByText(evalType + bpRendered.child(child), {
        exact: true,
      });
      expect(caption).toHaveAttribute("title", bpText["A3-02"]);
      expect(caption.textContent).not.toMatch(/model/i);
      expect(caption.getAttribute("title")).not.toMatch(/model/i);
    },
  );

  it("leaves an unpinned code child without a version claim or tooltip", () => {
    mountChild("code", null);
    const caption = screen.getByText("code", { exact: true });
    expect(caption).not.toHaveAttribute("title");
    expect(screen.queryByText(/recorded v/)).toBeNull();
  });
});
