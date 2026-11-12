import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { act, fireEvent, render, waitFor } from "src/utils/test-utils";
import { QueryInput } from "../QueryInput";

const TAG_FIELD = { value: "tag", label: "Tag", type: "string" };
const STATUS_FIELD = {
  value: "status",
  label: "Status",
  type: "enum",
  choices: ["OK", "ERROR"],
};
const OUTPUT_TYPE_FIELD = {
  value: "output_type",
  label: "Output Type",
  type: "string",
  choices: [
    { value: "pass_fail", label: "Pass/fail" },
    { value: "percentage", label: "Percentage" },
    { value: "deterministic", label: "Choices" },
  ],
};
const LATENCY_FIELD = { value: "latency", label: "Latency", type: "number" };
const DATE_FIELD = { value: "created_at", label: "Created at", type: "date" };

const LIST_OPERATORS = () => [
  { value: "in", label: "is any of" },
  { value: "not_in", label: "is none of" },
];
const RANGE_OPERATORS = () => [
  { value: "equals", label: "equals" },
  { value: "between", label: "between", range: true },
];

const UNFINISHED = "Unfinished filters are not applied.";

const renderQueryInput = ({ field, inputRef, ...props }) => {
  const onApply = vi.fn();
  const utils = render(
    <QueryInput
      ref={inputRef}
      filterFields={[field]}
      fieldMap={{ [field.value]: field }}
      onApply={onApply}
      {...props}
    />,
  );
  return { onApply, utils };
};

// Field options also carry a type chip, so pick the (only) field by keyboard.
const pickField = async (utils, typed) => {
  const input = utils.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: typed } });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() =>
    expect(utils.getByRole("combobox")).toHaveAttribute(
      "placeholder",
      "pick operator...",
    ),
  );
};

const pickOption = async (utils, typed, name) => {
  const input = utils.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: typed } });
  fireEvent.click(await utils.findByRole("option", { name }));
};

describe("W10-FILTERS-U07 Query tab dead ends", () => {
  it("W10-FILTERS-U07-operator-no-match says no operator matches and what to do", async () => {
    const { onApply, utils } = renderQueryInput({ field: TAG_FIELD });

    await pickField(utils, "Tag");
    const input = utils.getByRole("combobox");
    fireEvent.change(input, { target: { value: "zzz" } });

    expect(
      await utils.findByText(
        'No operator matches "zzz". Clear the text to see every operator.',
      ),
    ).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onApply).not.toHaveBeenCalled();
    expect(input).toHaveAttribute("placeholder", "pick operator...");
    expect(
      utils.getByText(`Pick an operator to finish this filter. ${UNFINISHED}`),
    ).toBeInTheDocument();
  });

  it("W10-FILTERS-U07-value-no-match explains a value outside a fixed list", async () => {
    const inputRef = createRef();
    const { onApply, utils } = renderQueryInput({
      field: STATUS_FIELD,
      inputRef,
    });

    await pickField(utils, "Status");
    await pickOption(utils, "is", /^is$/i);
    const input = utils.getByRole("combobox");
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "pick value..."),
    );
    fireEvent.change(input, { target: { value: "WARNING" } });

    expect(
      await utils.findByText(
        'No value matches "WARNING". Only listed values can be used; clear the text to see them.',
      ),
    ).toBeInTheDocument();
    expect(
      utils.getByText(
        `Pick a value from the list to finish this filter. ${UNFINISHED}`,
      ),
    ).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onApply).not.toHaveBeenCalled();
    expect(inputRef.current.flushPartial()).toBeNull();
  });

  it("W10-FILTERS-U07-unfinished-hint says a half-built filter is not applied until it is complete", async () => {
    const { utils } = renderQueryInput({ field: TAG_FIELD });

    await pickField(utils, "Tag");
    expect(
      utils.getByText(`Pick an operator to finish this filter. ${UNFINISHED}`),
    ).toBeInTheDocument();

    await pickOption(utils, "contains", /^contains$/i);
    const input = utils.getByRole("combobox");
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "type or pick value..."),
    );
    expect(
      utils.getByText(
        `Type or pick a value to finish this filter. ${UNFINISHED}`,
      ),
    ).toBeInTheDocument();

    // A typed value is what a close applies, so the warning goes away.
    fireEvent.change(input, { target: { value: "checkout" } });
    expect(utils.queryByText(new RegExp(UNFINISHED))).not.toBeInTheDocument();
  });
});

describe("W10-FILTERS-U08 editing a multi-value chip", () => {
  it("W10-FILTERS-U08-enter-keeps-list applies an edited list as a list", async () => {
    const { onApply, utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        { field: "tag", operator: "in", value: ["alpha", "beta"] },
      ],
    });

    fireEvent.click(utils.getByText("Tag is any of alpha, beta"));
    const input = utils.getByRole("combobox");
    expect(input).toHaveValue("alpha, beta");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "alpha, beta, gamma" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      { field: "tag", operator: "in", value: ["alpha", "beta", "gamma"] },
    ]);
  });

  it("W10-FILTERS-U08-suggestions-follow-last-value keeps suggestions and adds the picked value", async () => {
    const onValueSearchChange = vi.fn();
    const { onApply, utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      valueOptions: ["alpha", "beta", "gamma", "delta"],
      onValueSearchChange,
      initialTokens: [
        { field: "tag", operator: "in", value: ["alpha", "beta"] },
      ],
    });

    fireEvent.click(utils.getByText("Tag is any of alpha, beta"));
    expect(onValueSearchChange).not.toHaveBeenCalledWith("alpha, beta", "tag");
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "alpha, beta, ga" } });
    expect(onValueSearchChange).toHaveBeenLastCalledWith("ga", "tag");

    fireEvent.click(await utils.findByRole("option", { name: "gamma" }));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      { field: "tag", operator: "in", value: ["alpha", "beta", "gamma"] },
    ]);
  });

  it("W10-FILTERS-U08-fixed-choice-list keeps a fixed-choice list on close, by key", () => {
    const inputRef = createRef();
    const { utils } = renderQueryInput({
      field: OUTPUT_TYPE_FIELD,
      getOperators: LIST_OPERATORS,
      inputRef,
      initialTokens: [
        {
          field: "output_type",
          operator: "in",
          value: ["pass_fail", "deterministic"],
        },
      ],
    });

    fireEvent.click(
      utils.getByText("Output Type is any of Pass/fail, Choices"),
    );
    fireEvent.change(utils.getByRole("combobox"), {
      target: { value: "pass_fail, Percentage" },
    });

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      {
        field: "output_type",
        operator: "in",
        value: ["pass_fail", "percentage"],
      },
    ]);
  });

  it("W10-FILTERS-U08-unchanged-list re-applies an untouched list chip on close", () => {
    const inputRef = createRef();
    const { utils } = renderQueryInput({
      field: OUTPUT_TYPE_FIELD,
      getOperators: LIST_OPERATORS,
      inputRef,
      initialTokens: [
        {
          field: "output_type",
          operator: "in",
          value: ["pass_fail", "deterministic"],
        },
      ],
    });

    fireEvent.click(
      utils.getByText("Output Type is any of Pass/fail, Choices"),
    );

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      {
        field: "output_type",
        operator: "in",
        value: ["pass_fail", "deterministic"],
      },
    ]);
  });

  it("W10-FILTERS-U08-typed-ids keeps each kept value and its type", async () => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        {
          field: "custom_value",
          operator: "in",
          value: [1, true],
          valueTypes: ["number", "boolean"],
        },
      ],
    });

    fireEvent.click(utils.getByText("Custom value is any of 1, true"));
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "1, true, x" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "custom_value",
        operator: "in",
        value: [1, true, "x"],
        valueTypes: ["number", "boolean", undefined],
      },
    ]);
  });

  it("W10-FILTERS-U08-comma-value-kept keeps a kept value that holds a comma whole", async () => {
    const { onApply, utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        { field: "tag", operator: "in", value: ["hello, world", "beta"] },
      ],
    });

    fireEvent.click(utils.getByText("Tag is any of hello, world, beta"));
    const input = utils.getByRole("combobox");
    expect(input).toHaveValue("hello, world, beta");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "hello, world, gamma" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      { field: "tag", operator: "in", value: ["hello, world", "gamma"] },
    ]);
  });

  it("W10-FILTERS-U08-delete-beside-comma-value removes one value and keeps a comma value whole on close", () => {
    const inputRef = createRef();
    const { utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      inputRef,
      initialTokens: [
        {
          field: "tag",
          operator: "in",
          value: ["beta", "drop me", "hello, world"],
        },
      ],
    });

    fireEvent.click(
      utils.getByText("Tag is any of beta, drop me, hello, world"),
    );
    fireEvent.change(utils.getByRole("combobox"), {
      target: { value: "beta, hello, world" },
    });

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      { field: "tag", operator: "in", value: ["beta", "hello, world"] },
    ]);
  });

  it("W10-FILTERS-U08-exact-bytes keeps the spaces and line break of a kept value", async () => {
    const { onApply, utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        {
          field: "tag",
          operator: "in",
          value: ["Summarize this.\n", "  padded ", "beta"],
        },
      ],
    });

    fireEvent.click(utils.getByText(/^Tag is any of Summarize this\./));
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    // A text box shows no line breaks, so the kept value reads without one.
    fireEvent.change(input, {
      target: { value: "Summarize this.,   padded , gamma" },
    });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "tag",
        operator: "in",
        value: ["Summarize this.\n", "  padded ", "gamma"],
      },
    ]);
  });

  it("W10-FILTERS-U08-same-text-types keeps a text 1 and a number 1 apart with their types", async () => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        {
          field: "custom_value",
          operator: "in",
          value: ["1", 1],
          valueTypes: ["string", "number"],
        },
      ],
    });

    fireEvent.click(utils.getByText("Custom value is any of 1, 1"));
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "1, 1, x" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "custom_value",
        operator: "in",
        value: ["1", 1, "x"],
        valueTypes: ["string", "number", undefined],
      },
    ]);
  });

  // Control (passes before and after the fix): one value that contains a
  // comma is not a list and is never split.
  it("W10-FILTERS-U08-single-value keeps one value with a comma whole", async () => {
    const { onApply, utils } = renderQueryInput({
      field: TAG_FIELD,
      getOperators: LIST_OPERATORS,
      initialTokens: [
        { field: "tag", operator: "in", value: ["hello, world"] },
      ],
    });

    fireEvent.click(utils.getByText("Tag is any of hello, world"));
    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "hello, there" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      { field: "tag", operator: "in", value: "hello, there" },
    ]);
  });
});

describe("W10-FILTERS-U09 Query tab range editor", () => {
  const openNewRange = async (utils) => {
    await pickField(utils, "Latency");
    await pickOption(utils, "between", /^between$/i);
    return utils.findByPlaceholderText("from");
  };

  it("W10-FILTERS-U09-apply-name names the check button and shows a tooltip", async () => {
    const { utils } = renderQueryInput({
      field: LATENCY_FIELD,
      getOperators: RANGE_OPERATORS,
    });

    await openNewRange(utils);
    const apply = utils.getByRole("button", { name: "Apply range" });
    expect(apply).toBeDisabled();
    fireEvent.mouseOver(apply.parentElement);
    expect(await utils.findByRole("tooltip")).toHaveTextContent(
      "Apply range (Enter)",
    );
  });

  it("W10-FILTERS-U09-escape-cancels-edit restores the edited chip and keeps the panel open", async () => {
    const onApply = vi.fn();
    const outerKeyDown = vi.fn();
    const utils = render(
      <div onKeyDown={outerKeyDown}>
        <QueryInput
          filterFields={[LATENCY_FIELD]}
          fieldMap={{ [LATENCY_FIELD.value]: LATENCY_FIELD }}
          getOperators={RANGE_OPERATORS}
          onApply={onApply}
          initialTokens={[
            { field: "latency", operator: "between", value: ["1", "5"] },
          ]}
        />
      </div>,
    );

    fireEvent.click(utils.getByText("Latency between 1 – 5"));
    const from = utils.getByPlaceholderText("from");
    expect(from).toHaveValue("1");
    fireEvent.change(from, { target: { value: "2" } });
    fireEvent.keyDown(from, { key: "Escape" });

    const input = await utils.findByRole("combobox");
    expect(utils.queryByPlaceholderText("from")).not.toBeInTheDocument();
    expect(utils.getByText("Latency between 1 – 5")).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
    // The Escape must not reach the filter panel, which closes on Escape.
    expect(outerKeyDown).not.toHaveBeenCalled();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it("W10-FILTERS-U09-cancel-button discards a new range draft", async () => {
    const { onApply, utils } = renderQueryInput({
      field: LATENCY_FIELD,
      getOperators: RANGE_OPERATORS,
    });

    const from = await openNewRange(utils);
    fireEvent.change(from, { target: { value: "3" } });
    fireEvent.click(utils.getByRole("button", { name: "Cancel range" }));

    const input = await utils.findByRole("combobox");
    expect(input).toHaveAttribute(
      "placeholder",
      expect.stringContaining("type to filter"),
    );
    expect(utils.queryByText("between")).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("W10-FILTERS-U09-invalid-number says in words why a bound is invalid", async () => {
    const { utils } = renderQueryInput({
      field: LATENCY_FIELD,
      getOperators: RANGE_OPERATORS,
    });

    const from = await openNewRange(utils);
    fireEvent.change(from, { target: { value: "abc" } });

    const message = utils.getByText("Enter a number, like 10 or 2.5.");
    expect(message).toHaveAttribute("role", "alert");
    expect(from).toHaveAttribute("aria-invalid", "true");
    expect(from).toHaveAttribute("aria-describedby", message.id);
    expect(utils.getByRole("button", { name: "Apply range" })).toBeDisabled();
  });

  // Same unlabeled check button in the sibling date editor.
  it("W10-FILTERS-U09-date-apply-name names the date editor check button", async () => {
    const { utils } = renderQueryInput({
      field: DATE_FIELD,
      getOperators: () => [{ value: "greater_than", label: "after" }],
    });

    await pickField(utils, "Created at");
    await pickOption(utils, "after", /^after$/i);
    expect(
      await utils.findByRole("button", { name: "Apply date" }),
    ).toBeDisabled();
  });
});
