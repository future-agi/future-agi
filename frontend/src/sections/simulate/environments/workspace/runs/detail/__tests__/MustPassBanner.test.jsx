import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import MustPassBanner from "../MustPassBanner";

describe("MustPassBanner", () => {
  it("names each must-pass case the run failed", () => {
    render(<MustPassBanner names={["refund-once", "no-late-refund"]} />);
    expect(screen.getByTestId("must-pass-banner")).toHaveTextContent(
      "A must-pass case failed: refund-once, no-late-refund",
    );
  });

  it("shows nothing when no must-pass case failed", () => {
    render(<MustPassBanner names={[]} />);
    expect(screen.queryByTestId("must-pass-banner")).toBeNull();
  });
});
