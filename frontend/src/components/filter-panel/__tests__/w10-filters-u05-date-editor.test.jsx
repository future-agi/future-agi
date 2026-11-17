import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { act, fireEvent, render, waitFor } from "src/utils/test-utils";
import { QueryInput } from "../QueryInput";

// The Query tab must edit datetime columns the way the Basic tab does: a
// date + time picker whose value is the local 'yyyy-MM-ddTHH:mm' string.
const DATE_FIELD = {
  value: "created_at",
  label: "Created at",
  type: "date",
};

const DATE_OPS = [
  { value: "less_than", label: "before" },
  { value: "greater_than", label: "after" },
  { value: "equals", label: "on" },
  { value: "between", label: "between", range: true },
  { value: "is_null", label: "is null", noValue: true },
];

const renderDateQueryInput = ({ inputRef, ...props } = {}) => {
  const onApply = vi.fn();
  const utils = render(
    <QueryInput
      ref={inputRef}
      filterFields={[DATE_FIELD]}
      fieldMap={{ [DATE_FIELD.value]: DATE_FIELD }}
      getOperators={() => DATE_OPS}
      onApply={onApply}
      {...props}
    />,
  );
  return { onApply, utils };
};

const pickFromList = (utils, typed) => {
  const input = utils.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: typed } });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
};

const pickDateOperator = async (utils, operatorLabel) => {
  pickFromList(utils, "Created at");
  await waitFor(() =>
    expect(utils.getByRole("combobox")).toHaveAttribute(
      "placeholder",
      "pick operator...",
    ),
  );
  pickFromList(utils, operatorLabel);
};

describe("W10-FILTERS-U05 Query tab date editor", () => {
  it("W10-FILTERS-U05-range-reopen shows the stored date and time in both bounds", () => {
    const { utils } = renderDateQueryInput({
      initialTokens: [
        {
          field: "created_at",
          operator: "between",
          value: ["2026-11-01T09:30", "2026-11-05T17:00"],
        },
      ],
    });

    fireEvent.click(
      utils.getByText("Created at between 2026-11-01T09:30 – 2026-11-05T17:00"),
    );

    const from = utils.getByPlaceholderText("from");
    const to = utils.getByPlaceholderText("to");
    expect(from).toHaveAttribute("type", "datetime-local");
    expect(to).toHaveAttribute("type", "datetime-local");
    expect(from).toHaveValue("2026-11-01T09:30");
    expect(to).toHaveValue("2026-11-05T17:00");
  });

  it("W10-FILTERS-U05-range-pick commits the picked local date and time", async () => {
    const { onApply, utils } = renderDateQueryInput();

    await pickDateOperator(utils, "between");
    const from = await utils.findByPlaceholderText("from");
    const to = utils.getByPlaceholderText("to");
    fireEvent.change(from, { target: { value: "2026-11-01T09:00" } });
    fireEvent.change(to, { target: { value: "2026-11-05T17:00" } });
    fireEvent.keyDown(to, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "created_at",
        operator: "between",
        value: ["2026-11-01T09:00", "2026-11-05T17:00"],
      },
    ]);
  });

  it("W10-FILTERS-U05-range-date-only reopens a day range as whole days", () => {
    const { utils } = renderDateQueryInput({
      initialTokens: [
        {
          field: "created_at",
          operator: "between",
          value: ["2026-11-01", "2026-11-05"],
        },
      ],
    });

    fireEvent.click(
      utils.getByText("Created at between 2026-11-01 – 2026-11-05"),
    );

    expect(utils.getByPlaceholderText("from")).toHaveValue("2026-11-01T00:00");
    expect(utils.getByPlaceholderText("to")).toHaveValue("2026-11-05T23:59");
  });

  it("W10-FILTERS-U05-single-pick gives after a date and time picker", async () => {
    const { onApply, utils } = renderDateQueryInput();

    await pickDateOperator(utils, "after");
    const value = await utils.findByLabelText("Date and time");
    expect(value).toHaveAttribute("type", "datetime-local");
    fireEvent.change(value, { target: { value: "2026-11-01T09:00" } });
    fireEvent.keyDown(value, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "created_at",
        operator: "greater_than",
        value: "2026-11-01T09:00",
      },
    ]);
  });

  it("W10-FILTERS-U05-single-reopen shows the stored date and time", () => {
    const { utils } = renderDateQueryInput({
      initialTokens: [
        {
          field: "created_at",
          operator: "greater_than",
          value: "2026-11-01T09:30",
        },
      ],
    });

    fireEvent.click(utils.getByText("Created at after 2026-11-01T09:30"));

    const value = utils.getByLabelText("Date and time");
    expect(value).toHaveAttribute("type", "datetime-local");
    expect(value).toHaveValue("2026-11-01T09:30");
  });

  it("W10-FILTERS-U05-single-flush applies a picked date that was not confirmed", async () => {
    const inputRef = createRef();
    const { onApply, utils } = renderDateQueryInput({ inputRef });

    await pickDateOperator(utils, "before");
    const value = await utils.findByLabelText("Date and time");
    fireEvent.change(value, { target: { value: "2026-11-05T17:00" } });

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      {
        field: "created_at",
        operator: "less_than",
        value: "2026-11-05T17:00",
      },
    ]);
    expect(onApply).not.toHaveBeenCalled();
  });
});
