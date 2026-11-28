import { describe, expect, it } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import SecretKeyRenderer from "../SecretKeyRenderer";

describe("SecretKeyRenderer", () => {
  it("explains why masked list keys cannot be copied", async () => {
    const user = userEvent.setup();
    render(<SecretKeyRenderer value="abcd**********1234" />);

    const masked = screen.getByText("abcd**********1234");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    await user.tab();
    expect(masked).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(/create a new key/i);
    expect(masked).toHaveAccessibleDescription(/only be copied when created/i);
  });

  it("masks raw fallback values defensively", () => {
    render(<SecretKeyRenderer value="1234567890abcdef1234567890abcdef" />);

    expect(screen.getByText("1234**********cdef")).toBeInTheDocument();
    expect(
      screen.queryByText("1234567890abcdef1234567890abcdef"),
    ).not.toBeInTheDocument();
  });
});
