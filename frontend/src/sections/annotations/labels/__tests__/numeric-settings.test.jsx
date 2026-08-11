/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { Button } from "@mui/material";
import { useForm } from "react-hook-form";
import { render, screen, userEvent } from "src/utils/test-utils";
import NumericSettings from "../settings/numeric-settings";

function NumericSettingsHarness({ defaultValues, onSubmit }) {
  const methods = useForm({ defaultValues });
  return (
    <form onSubmit={methods.handleSubmit(onSubmit)}>
      <NumericSettings control={methods.control} />
      <Button type="button" onClick={() => methods.trigger()}>
        Validate
      </Button>
      <Button type="submit">Save</Button>
    </form>
  );
}

describe("NumericSettings", () => {
  it("rejects negative bounds and non-positive step size", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: {
            min: -1,
            max: -2,
            step_size: 0,
            display_type: "button",
          },
        }}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole("button", { name: /validate/i }));

    expect(
      await screen.findByText("Minimum cannot be negative"),
    ).toBeInTheDocument();
    expect(screen.getByText("Maximum cannot be negative")).toBeInTheDocument();
    expect(screen.getByText("Step size must be positive")).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
