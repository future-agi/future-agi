import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import SecretField, { isMaskedSecret } from "./SecretField";

const MASK = "__encrypted__9f8e7d6c5b4a3210";

describe("isMaskedSecret", () => {
  it("recognizes only the exact mask a read path returns", () => {
    expect(isMaskedSecret(MASK)).toBe(true);
    expect(isMaskedSecret("hunter2")).toBe(false);
    expect(isMaskedSecret("")).toBe(false);
    expect(isMaskedSecret(undefined)).toBe(false);
  });

  it("does not take a clear value for a stored secret because of its prefix", () => {
    // No read path returns ciphertext, and a secret may be any string: these
    // are values a user can type, not stored secrets.
    expect(isMaskedSecret("enc::gAAAAAB...")).toBe(false);
    expect(isMaskedSecret("enc::hunter2")).toBe(false);
    expect(isMaskedSecret("__encrypted__")).toBe(false);
    expect(isMaskedSecret("__encrypted__my-own-token")).toBe(false);
    expect(isMaskedSecret(`${MASK}0`)).toBe(false);
  });
});

// The field the way the editor tabs hold it: the draft is the parent's state,
// which the field's onChange updates, and a load replaces it from the server.
const Controlled = () => {
  const [value, setValue] = React.useState("");
  return (
    <>
      <SecretField label="Password" value={value} onChange={setValue} />
      <button type="button" onClick={() => setValue(MASK)}>
        load stored
      </button>
    </>
  );
};

describe("SecretField while a value is typed", () => {
  it.each([
    ["a value that starts with enc::", "enc::hunter2"],
    ["a value shaped like the read mask", "__encrypted__0123456789abcdef"],
  ])("keeps %s editable and sends it", async (_label, typed) => {
    const user = userEvent.setup();
    render(<Controlled />);

    await user.type(screen.getByLabelText("Password"), typed);

    const input = screen.getByLabelText("Password");
    expect(input).not.toHaveAttribute("readonly");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue(typed);
    expect(screen.queryByRole("button", { name: /replace/i })).toBeNull();
  });

  it("shows dots again when a stored secret is loaded over a typed draft", async () => {
    const user = userEvent.setup();
    render(<Controlled />);

    await user.type(screen.getByLabelText("Password"), "draft");
    await user.click(screen.getByRole("button", { name: "load stored" }));

    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("readonly");
    expect(input.value).toMatch(/•+/);
    expect(
      screen.getByRole("button", { name: /replace/i }),
    ).toBeInTheDocument();
  });
});

describe("SecretField with a stored secret", () => {
  it("shows placeholder dots and a Replace action, never the mask", () => {
    const onChange = vi.fn();
    render(<SecretField label="Password" value={MASK} onChange={onChange} />);

    const input = screen.getByLabelText("Password");
    expect(input.value).not.toContain("__encrypted__");
    expect(input.value).toMatch(/•+/);
    expect(input).toHaveAttribute("readonly");
    expect(
      screen.getByRole("button", { name: /replace/i }),
    ).toBeInTheDocument();
  });

  it("clears the draft to an empty editable value when Replace is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SecretField label="Password" value={MASK} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /replace/i }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("sends null to delete a stored secret when Remove is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SecretField label="Password" value={MASK} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: /remove/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("hides Remove when removable is false", () => {
    render(
      <SecretField
        label="Password"
        value={MASK}
        onChange={vi.fn()}
        removable={false}
      />,
    );
    expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();
    expect(
      screen.getByRole("button", { name: /replace/i }),
    ).toBeInTheDocument();
  });
});

describe("SecretField with no stored secret", () => {
  it("renders an editable password input and reports typed text", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SecretField label="API Key" value="" onChange={onChange} />);

    const input = screen.getByLabelText("API Key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).not.toHaveAttribute("readonly");
    await user.type(input, "k");
    expect(onChange).toHaveBeenCalledWith("k");
    expect(screen.queryByRole("button", { name: /replace/i })).toBeNull();
  });

  it("never renders a mask as the input value even mid-edit", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <SecretField label="Token" value="" onChange={onChange} />,
    );
    rerender(
      <SecretField label="Token" value="typed-value" onChange={onChange} />,
    );
    expect(screen.getByLabelText("Token").value).toBe("typed-value");
  });
});
