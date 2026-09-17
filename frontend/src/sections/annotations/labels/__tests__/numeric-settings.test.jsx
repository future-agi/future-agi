/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { Button } from "@mui/material";
import { useForm } from "react-hook-form";
import { enqueueSnackbar } from "notistack";
import { render, screen, userEvent } from "src/utils/test-utils";
import NumericSettings from "../settings/numeric-settings";

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

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

  it("does not keep a leading zero when typing over the default value", async () => {
    const user = userEvent.setup();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 10, step_size: 1, display_type: "slider" },
        }}
        onSubmit={vi.fn()}
      />,
    );

    const minInput = screen.getByLabelText(/minimum/i);
    await user.type(minInput, "5");

    expect(minInput).toHaveValue(5);
  });

  it("blocks values above the cap on maximum and warns instead", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <NumericSettingsHarness
        defaultValues={{
          settings: { min: 0, max: 1, step_size: 1, display_type: "slider" },
        }}
        onSubmit={onSubmit}
      />,
    );

    const maxInput = screen.getByLabelText(/maximum/i);
    await user.clear(maxInput);
    await user.type(maxInput, "11");

    expect(maxInput).toHaveValue(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Maximum value is 10",
      expect.objectContaining({ variant: "warning" }),
    );
  });
});
