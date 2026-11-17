import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import NumberQuickFilterPopover from "../NumberQuickFilterPopover";
import NumberQuickFilterValue from "../NumberQuickFilterValue";
import { NumberQuickFilterValidationSchema } from "../validation";

// The number quick filter opened from a list cell. No column it reads holds a
// number below 0, so a typed minus stays in the field and Apply is refused
// with a sentence that says what to enter. "Required" is only for a field
// with no number in it, and a number of 0 or more applies as before.

// Every number field draws these two arrows.
for (const name of [
  "solar:alt-arrow-up-linear",
  "solar:alt-arrow-down-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const BELOW_ZERO = "Enter a value of 0 or more";

const latencyFilter = {
  column_id: "latency",
  display_name: "Latency",
  filter_config: {
    filter_type: "number",
    filter_op: "equals",
    filter_value: "",
  },
};

const openPopover = (cellValue) => {
  const setFilters = vi.fn();
  const onClose = vi.fn();
  render(
    <NumberQuickFilterPopover
      open
      filterData={{
        value: cellValue,
        filter: latencyFilter,
        filterAnchor: { top: 120, left: 240 },
      }}
      onClose={onClose}
      setFilters={setFilters}
    />,
  );
  return { setFilters, onClose };
};

// The range fields with the popover's own form rule, already on "between".
// "Use one value" switches to a single-value operator, as the popover's
// operator menu does.
function RangeFields({ onApply }) {
  const { control, handleSubmit, setValue } = useForm({
    defaultValues: { operator: "between", value1: 10, value2: 20 },
    resolver: zodResolver(NumberQuickFilterValidationSchema),
  });
  return (
    <form onSubmit={handleSubmit(onApply)}>
      <NumberQuickFilterValue control={control} />
      <button type="button" onClick={() => setValue("operator", "equals")}>
        Use one value
      </button>
      <button type="submit">Apply</button>
    </form>
  );
}

RangeFields.propTypes = {
  onApply: PropTypes.func,
};

const typeInto = async (input, value) => {
  await act(async () => {
    fireEvent.change(input, { target: { value } });
  });
};

const pressApply = async () => {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  });
};

describe("Number quick filter, a number below 0", () => {
  it("keeps a typed -5 in Selected Value and refuses it with the sentence, not Required", async () => {
    const { setFilters, onClose } = openPopover(40);
    const input = screen.getByPlaceholderText("Enter value");

    await typeInto(input, "-5");
    expect(input).toHaveValue(-5);

    await pressApply();

    expect(await screen.findByText(BELOW_ZERO)).toBeInTheDocument();
    expect(screen.queryByText("Required")).not.toBeInTheDocument();
    expect(setFilters).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps a typed -5 in Min and refuses it with the sentence", async () => {
    const onApply = vi.fn();
    render(<RangeFields onApply={onApply} />);
    const min = screen.getByPlaceholderText("Enter min value");

    await typeInto(min, "-5");
    expect(min).toHaveValue(-5);

    await pressApply();

    expect(await screen.findByText(BELOW_ZERO)).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("keeps a typed -5 in Max and refuses it with the sentence", async () => {
    const onApply = vi.fn();
    render(<RangeFields onApply={onApply} />);
    const max = screen.getByPlaceholderText("Enter max value");

    await typeInto(max, "-5");
    expect(max).toHaveValue(-5);

    await pressApply();

    expect(await screen.findByText(BELOW_ZERO)).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("does not let a minus left in a hidden Max block a single value", async () => {
    const onApply = vi.fn();
    render(<RangeFields onApply={onApply} />);

    await typeInto(screen.getByPlaceholderText("Enter max value"), "-5");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use one value" }));
    });
    expect(screen.queryByPlaceholderText("Enter max value")).toBeNull();

    await pressApply();

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply.mock.calls[0][0]).toMatchObject({
      operator: "equals",
      value1: 10,
    });
  });
});

describe("Number quick filter, an empty field", () => {
  it("says Required when the cell gave no value", async () => {
    const { setFilters } = openPopover(undefined);

    await pressApply();

    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(screen.queryByText(BELOW_ZERO)).not.toBeInTheDocument();
    expect(setFilters).not.toHaveBeenCalled();
  });

  it("says Required when the value is cleared", async () => {
    const { setFilters } = openPopover(40);

    await typeInto(screen.getByPlaceholderText("Enter value"), "");
    await pressApply();

    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(setFilters).not.toHaveBeenCalled();
  });
});

describe("Number quick filter, a number of 0 or more", () => {
  it("applies a typed value to the column and closes", async () => {
    const { setFilters, onClose } = openPopover(40);

    await typeInto(screen.getByPlaceholderText("Enter value"), "12.5");
    await pressApply();

    await waitFor(() => expect(setFilters).toHaveBeenCalledTimes(1));
    const [applied] = setFilters.mock.calls[0][0]([]);
    expect(applied.column_id).toBe("latency");
    expect(applied.filter_config.filter_op).toBe("equals");
    expect(applied.filter_config.filter_value).toBe(12.5);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("applies a range that starts at 0", async () => {
    const onApply = vi.fn();
    render(<RangeFields onApply={onApply} />);

    await typeInto(screen.getByPlaceholderText("Enter min value"), "0");
    await pressApply();

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply.mock.calls[0][0]).toEqual({
      operator: "between",
      value1: 0,
      value2: 20,
    });
    expect(screen.queryByText(BELOW_ZERO)).not.toBeInTheDocument();
  });
});
