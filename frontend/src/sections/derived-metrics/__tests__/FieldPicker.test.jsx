import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FieldPicker from "../FieldPicker";
import { supportedSourceOptions } from "../builderSpec";
import { capabilities, catalog, evaluators } from "./fixtures/builder";

afterEach(cleanup);
it("keeps a typed source search across catalog refreshes without rebinding the saved source", () => {
  const options = supportedSourceOptions(catalog, evaluators, capabilities);
  const input = { ...options[0].descriptor, alias: "value", reducer: "latest" };
  const onChange = vi.fn();
  const onSearch = vi.fn();
  const view = render(
    <FieldPicker
      input={input}
      options={options}
      onChange={onChange}
      onSearch={onSearch}
    />,
  );
  const field = screen.getByRole("combobox", { name: "Field" });
  expect(field.value).toBe("Locale");
  fireEvent.change(field, { target: { value: "Quality" } });
  view.rerender(
    <FieldPicker
      input={{ ...input }}
      options={options.map((option) => ({ ...option }))}
      onChange={onChange}
      onSearch={onSearch}
    />,
  );
  expect(field.value).toBe("Quality");
  expect(onSearch).toHaveBeenCalledWith("Quality");
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.blur(field);
  expect(field.value).toBe("Locale");
  expect(onChange).not.toHaveBeenCalled();
});
