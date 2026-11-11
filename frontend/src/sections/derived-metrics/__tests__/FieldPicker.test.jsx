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

// Round 3 UI: each option says what it holds, outside its accessible name,
// and the empty list names the project and the search.
it("shows each source's type beside it without adding it to the option's name", () => {
  const options = supportedSourceOptions(catalog, evaluators, capabilities);
  render(
    <FieldPicker
      options={options}
      onChange={vi.fn()}
      label="Choose a source"
    />,
  );
  fireEvent.mouseDown(
    screen.getByRole("combobox", { name: "Choose a source" }),
  );
  expect(screen.getByRole("option", { name: "Resolved" })).toHaveTextContent(
    "Yes/no",
  );
  expect(screen.getByRole("option", { name: "Quality" })).toHaveTextContent(
    "Evaluation",
  );
});
it("says which project and search found no supported field", () => {
  render(
    <FieldPicker
      options={[]}
      onChange={vi.fn()}
      onSearch={vi.fn()}
      label="Choose a source"
      projectName="Support"
    />,
  );
  const field = screen.getByRole("combobox", { name: "Choose a source" });
  fireEvent.mouseDown(field);
  expect(
    screen.getByText("No fields or saved evaluations in Support"),
  ).toBeTruthy();
  fireEvent.change(field, { target: { value: "zz" } });
  expect(
    screen.getByText("No fields or saved evaluations match “zz” in Support"),
  ).toBeTruthy();
});
