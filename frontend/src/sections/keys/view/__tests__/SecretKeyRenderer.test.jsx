import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SecretKeyRenderer from "../SecretKeyRenderer";

describe("SecretKeyRenderer", () => {
  it("does not expose copy controls for masked list keys", () => {
    render(<SecretKeyRenderer value="abcd**********1234" />);

    expect(screen.getByText("abcd**********1234")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("masks raw fallback values defensively", () => {
    render(<SecretKeyRenderer value="1234567890abcdef1234567890abcdef" />);

    expect(screen.getByText("1234**********cdef")).toBeInTheDocument();
    expect(
      screen.queryByText("1234567890abcdef1234567890abcdef"),
    ).not.toBeInTheDocument();
  });
});
