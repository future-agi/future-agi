import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SecretKeyRenderer from "../SecretKeyRenderer";

describe("SecretKeyRenderer", () => {
  it("does not expose copy controls for masked list keys", () => {
    render(<SecretKeyRenderer value="abcd**********1234" />);

    expect(screen.getByText("abcd**********1234")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows eight stars for a raw value, never a hint built from it", () => {
    const { container } = render(
      <SecretKeyRenderer value="1234567890abcdef1234567890abcdef" />,
    );

    expect(container.textContent).toBe("********");
    expect(screen.queryByText("1234**********cdef")).not.toBeInTheDocument();
    expect(container.innerHTML).not.toContain(
      "1234567890abcdef1234567890abcdef",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
