import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import VersionHeader from "../VersionHeader";

describe("a version's origin", () => {
  it("names Epoch for a version an epoch made, never its code", () => {
    render(<VersionHeader version={{ label: "v4", origin: "fix" }} />);
    expect(screen.getByText("Epoch")).toBeInTheDocument();
    expect(screen.queryByText("fix")).toBeNull();
  });
});
