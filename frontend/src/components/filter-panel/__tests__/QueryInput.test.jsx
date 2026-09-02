import { describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { act, fireEvent, render, waitFor } from "src/utils/test-utils";
import { QueryInput } from "../FilterPanel";

const selectPhaseOption = async (utils, typed, nextPlaceholder) => {
  const input = utils.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: typed } });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() =>
    expect(utils.getByRole("combobox")).toHaveAttribute(
      "placeholder",
      nextPlaceholder,
    ),
  );
};

const renderQueryInput = ({
  field,
  valueOptions = [],
  inputRef,
  ...queryInputProps
}) => {
  const onApply = vi.fn();
  const utils = render(
    <QueryInput
      ref={inputRef}
      filterFields={[field]}
      fieldMap={{ [field.value]: field }}
      onApply={onApply}
      valueOptions={valueOptions}
      {...queryInputProps}
    />,
  );
  return { onApply, utils };
};

describe("QueryInput explicit values", () => {
  it("commits typed text when sampled suggestions are fuzzy, not exact", async () => {
    const field = {
      value: "final_status",
      label: "Final status",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      valueOptions: ["Rechazado parcialmente"],
    });

    await selectPhaseOption(utils, "Final status", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Rechazado" } });
    expect(
      await utils.findByText("Rechazado parcialmente"),
    ).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "final_status",
        operator: "contains",
        value: "Rechazado",
      },
    ]);
  });

  it("commits the exact option even when an earlier suggestion is fuzzy", async () => {
    const field = {
      value: "final_status",
      label: "Final status",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      valueOptions: [
        { value: "Rechazado parcialmente", label: "Rechazado parcialmente" },
        { value: "Rechazado", label: "Rechazado" },
      ],
    });

    await selectPhaseOption(utils, "Final status", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "Rechazado" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "final_status",
        operator: "contains",
        value: "Rechazado",
      },
    ]);
  });

  it.each([
    [false, "false"],
    [0, "0"],
  ])("preserves the typed option id %p", async (optionValue, typedValue) => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      valueOptions: [{ value: optionValue, label: typedValue }],
    });

    await selectPhaseOption(utils, "Custom value", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: typedValue } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "custom_value",
        operator: "contains",
        value: optionValue,
      },
    ]);
  });

  it("preserves the selected ClickHouse storage family", async () => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { onApply, utils } = renderQueryInput({
      field,
      valueOptions: [
        { value: "1", label: "string one", type: "string" },
        { value: 1, label: "number one", type: "number" },
        { value: true, label: "boolean true", type: "boolean" },
      ],
    });

    await selectPhaseOption(utils, "Custom value", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");
    fireEvent.click(await utils.findByText("number one"));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "custom_value",
        operator: "contains",
        value: 1,
        valueTypes: ["number"],
      },
    ]);
  });

  it("keeps an annotation choice value distinct from its display label", async () => {
    const field = {
      value: "annotation-label",
      label: "Annotation",
      type: "categorical",
      choices: [
        { value: "customer_refund", label: "Customer refund requested" },
      ],
      allowCustomValue: true,
    };
    const { onApply, utils } = renderQueryInput({ field });

    await selectPhaseOption(utils, "Annotation", "pick operator...");
    const input = utils.getByRole("combobox");
    fireEvent.change(input, { target: { value: "equals" } });
    fireEvent.click(await utils.findByRole("option", { name: /^equals$/i }));
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "type or pick value..."),
    );
    fireEvent.change(input, { target: { value: "customer_refund" } });
    fireEvent.click(await utils.findByText("Customer refund requested"));

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "annotation-label",
        operator: "equals",
        value: "customer_refund",
      },
    ]);
  });

  it("allows an exact stored-only annotation value beside configured choices", async () => {
    const field = {
      value: "annotation-label",
      label: "Annotation",
      type: "categorical",
      choices: [{ value: "configured", label: "Configured" }],
      allowCustomValue: true,
    };
    const { onApply, utils } = renderQueryInput({ field });

    await selectPhaseOption(utils, "Annotation", "pick operator...");
    const input = utils.getByRole("combobox");
    fireEvent.change(input, { target: { value: "equals" } });
    fireEvent.click(await utils.findByRole("option", { name: /^equals$/i }));
    await waitFor(() =>
      expect(input).toHaveAttribute("placeholder", "type or pick value..."),
    );
    fireEvent.change(input, { target: { value: "historical-only" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
    expect(onApply).toHaveBeenLastCalledWith([
      {
        field: "annotation-label",
        operator: "equals",
        value: "historical-only",
      },
    ]);
  });

  it("requests server search and the next value page", async () => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const onValueSearchChange = vi.fn();
    const onLoadMoreValues = vi.fn();
    const { utils } = renderQueryInput({
      field,
      valueOptions: Array.from({ length: 12 }, (_, index) => ({
        value: `value-${index}`,
        label: `value-${index}`,
        type: "string",
      })),
      onValueSearchChange,
      onLoadMoreValues,
      hasMoreValues: true,
    });

    await selectPhaseOption(utils, "Custom value", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.change(input, { target: { value: "value" } });
    expect(onValueSearchChange).toHaveBeenLastCalledWith(
      "value",
      "custom_value",
    );

    const listbox = await utils.findByRole("listbox");
    let scrollTop = 180;
    Object.defineProperties(listbox, {
      scrollTop: { configurable: true, get: () => scrollTop },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(listbox);
    fireEvent.scroll(listbox);
    fireEvent.scroll(listbox);
    expect(onLoadMoreValues).toHaveBeenCalledOnce();
    expect(utils.queryByText("Load more values")).not.toBeInTheDocument();

    await act(async () => Promise.resolve());
    scrollTop = 80;
    fireEvent.scroll(listbox);
    scrollTop = 180;
    fireEvent.scroll(listbox);
    expect(onLoadMoreValues).toHaveBeenCalledTimes(2);
  });

  it("keeps failed value pagination retryable without a selectable continuation row", async () => {
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    let resolvePage;
    const onLoadMoreValues = vi.fn(
      () => new Promise((resolve) => (resolvePage = resolve)),
    );
    const { utils } = renderQueryInput({
      field,
      valueOptions: [
        { value: "already-loaded", label: "already-loaded", type: "string" },
      ],
      onLoadMoreValues,
      hasMoreValues: true,
      valueLoadError: true,
    });

    await selectPhaseOption(utils, "Custom value", "pick operator...");
    await selectPhaseOption(utils, "Contains", "type or pick value...");

    const retry = utils.getByRole("button", {
      name: "Retry loading values",
    });
    fireEvent.click(retry);
    fireEvent.click(retry);

    expect(onLoadMoreValues).toHaveBeenCalledOnce();
    expect(utils.queryByText("Load more values")).not.toBeInTheDocument();
    expect(
      utils.getByText(
        "More values could not be loaded. Loaded matches remain available.",
      ),
    ).toHaveAttribute("role", "status");

    await act(async () => resolvePage());
  });

  it("requests exact field search and advances field discovery near the list end", async () => {
    const field = {
      value: "final_status",
      label: "Final status",
      type: "string",
    };
    const onFieldSearchChange = vi.fn();
    const onLoadMoreFields = vi.fn();
    const { utils } = renderQueryInput({
      field,
      onFieldSearchChange,
      onLoadMoreFields,
      hasMoreFields: true,
    });

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "final" } });

    expect(onFieldSearchChange).toHaveBeenLastCalledWith("final");
    const listbox = await utils.findByRole("listbox");
    Object.defineProperties(listbox, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(listbox);
    fireEvent.scroll(listbox);
    expect(onLoadMoreFields).toHaveBeenCalledOnce();
    expect(utils.queryByText("Load more fields")).not.toBeInTheDocument();
    expect(input).toHaveValue("final");
  });

  it("fetches and preserves type when an existing token is edited", async () => {
    const inputRef = createRef();
    const onFieldChange = vi.fn();
    const onValueSearchChange = vi.fn();
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { utils } = renderQueryInput({
      field,
      inputRef,
      initialTokens: [
        {
          field: "custom_value",
          operator: "contains",
          value: 1,
          valueTypes: ["number"],
        },
      ],
      onFieldChange,
      onValueSearchChange,
    });

    fireEvent.click(utils.getByText("Custom value Contains 1"));

    expect(onFieldChange).toHaveBeenCalledWith("custom_value");
    expect(onValueSearchChange).toHaveBeenCalledWith("1", "custom_value");

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      {
        field: "custom_value",
        operator: "contains",
        value: 1,
        valueTypes: ["number"],
      },
    ]);
  });

  it("drops stale type provenance when an edited value changes", () => {
    const inputRef = createRef();
    const field = {
      value: "custom_value",
      label: "Custom value",
      type: "string",
    };
    const { utils } = renderQueryInput({
      field,
      inputRef,
      initialTokens: [
        {
          field: "custom_value",
          operator: "contains",
          value: 1,
          valueTypes: ["number"],
        },
      ],
    });

    fireEvent.click(utils.getByText("Custom value Contains 1"));
    fireEvent.change(utils.getByRole("combobox"), {
      target: { value: "abc" },
    });

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      {
        field: "custom_value",
        operator: "contains",
        value: "abc",
      },
    ]);
  });

  it("does not accept arbitrary text for fields with fixed choices", async () => {
    const inputRef = createRef();
    const field = {
      value: "status",
      label: "Status",
      type: "enum",
      choices: ["OK", "ERROR"],
    };
    const { onApply, utils } = renderQueryInput({ field, inputRef });

    await selectPhaseOption(utils, "Status", "pick operator...");
    await selectPhaseOption(utils, "Is", "pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "WARNING" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onApply).not.toHaveBeenCalled();
    expect(input).toHaveAttribute("placeholder", "pick value...");
    expect(input).toHaveValue("WARNING");
    expect(inputRef.current.flushPartial()).toBeNull();
  });

  it("flushes only a case-insensitive exact fixed choice", async () => {
    const inputRef = createRef();
    const field = {
      value: "status",
      label: "Status",
      type: "enum",
      choices: ["OK", "ERROR"],
    };
    const { onApply, utils } = renderQueryInput({ field, inputRef });

    await selectPhaseOption(utils, "Status", "pick operator...");
    await selectPhaseOption(utils, "Is", "pick value...");

    const input = utils.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "ok" } });

    let flushed;
    act(() => {
      flushed = inputRef.current.flushPartial();
    });
    expect(flushed).toEqual([
      { field: "status", operator: "is_not", value: "OK" },
    ]);
    expect(onApply).not.toHaveBeenCalled();
  });
});
