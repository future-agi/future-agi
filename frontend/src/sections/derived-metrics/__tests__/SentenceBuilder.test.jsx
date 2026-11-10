import PropTypes from "prop-types";
import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: () => ({
    data: [{ value: "fr" }, { value: "en" }],
  }),
}));
vi.mock("../EvaluatorBinding", () => ({ default: () => null }));
import SentenceBuilder from "../SentenceBuilder";
import { newBuilder, supportedSourceOptions } from "../builderSpec";
import { capabilities, catalog, evaluators, id } from "./fixtures/builder";
const options = supportedSourceOptions(
  catalog,
  evaluators,
  capabilities,
  "Support",
);
afterEach(cleanup);
function Harness({ sourceLabels }) {
  const [form, setForm] = useState({ ...newBuilder(id), name: "Resolution" });
  return (
    <>
      <SentenceBuilder
        form={form}
        capabilities={capabilities}
        options={options}
        onChange={setForm}
        sourceLabels={sourceLabels}
      />
      <output data-testid="wire">{JSON.stringify(form)}</output>
    </>
  );
}
Harness.propTypes = { sourceLabels: PropTypes.objectOf(PropTypes.string) };
const wire = () => JSON.parse(screen.getByTestId("wire").textContent);
const choose = (label, text) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: text }));
};
describe("sentence controls", () => {
  it("creates the simplest boolean metric entirely with picker selections", () => {
    render(<Harness />);
    choose("Choose a source", "Resolved");
    expect(wire().inputs[0]).toMatchObject({
      source: "span",
      attribute_key: "resolved",
      value_type: "boolean",
      reducer: "latest",
      ordering: "sentence",
    });
    expect(wire().field_spec.conditions[0].value).toBe(true);
    choose("Value for resolved", "False");
    expect(wire().field_spec.conditions[0].value).toBe(false);
    expect(
      screen.getByText(/Each source is reduced independently/),
    ).toBeTruthy();
  });
  it("selects locale=fr without typing a field or value", () => {
    render(<Harness />);
    choose("Choose a source", "Locale");
    choose("Value for locale", "fr");
    expect(wire().field_spec.conditions).toEqual([
      { alias: "value_1", operator: "eq", value: "fr" },
    ]);
  });
  it("supports keyboard selection and never exposes trace or new user units without their gate", () => {
    render(<Harness />);
    const picker = screen.getByRole("combobox", { name: "Choose a source" });
    act(() => picker.focus());
    fireEvent.keyDown(picker, { key: "ArrowDown" });
    fireEvent.keyDown(picker, { key: "ArrowDown" });
    fireEvent.keyDown(picker, { key: "Enter" });
    expect(wire().inputs).toHaveLength(1);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Unit" }));
    expect(screen.queryByRole("option", { name: "traces" })).toBeNull();
    expect(screen.queryByRole("option", { name: "users" })).toBeNull();
  });
  it("keeps labels optional and removes them from numeric metrics", () => {
    render(<Harness />);
    expect(screen.queryByLabelText("Show matches as")).toBeNull();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Customize match labels" }),
    );
    fireEvent.change(screen.getByLabelText("Show matches as"), {
      target: { value: "Resolved" },
    });
    expect(wire().value_labels.match).toBe("Resolved");
    choose("Calculation", "Average");
    expect(
      screen.queryByRole("checkbox", { name: "Customize match labels" }),
    ).toBeNull();
    expect(wire().value_labels).toEqual({});
  });
  it("moves a text comparison to an explicit any-observation predicate", () => {
    render(<Harness />);
    choose("Choose a source", "Locale");
    choose("Value for locale", "fr");
    choose(
      "When a conversation has several values, use",
      "any observation matches the condition",
    );
    expect(wire().inputs[0]).toMatchObject({
      reducer: "any",
      condition_stage: "observation",
      condition: { operator: "eq", value: "fr" },
    });
    expect(wire().field_spec.conditions[0]).toEqual({
      alias: "value_1",
      operator: "eq",
      value: true,
    });
  });
  it("combines independently reduced raw attributes and a persisted evaluation", () => {
    render(<Harness />);
    choose("Choose a source", "Resolved");
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
    choose("Choose a source", "Quality");
    expect(wire().inputs[1]).toMatchObject({
      source: "eval",
      config_id: id,
      config_hash: "a".repeat(64),
      value_type: "number",
    });
    expect(wire().field_spec.conditions).toHaveLength(2);
  });
  it("keeps the optional label line collapsed for saved server defaults", () => {
    render(
      <SentenceBuilder
        form={{
          ...newBuilder(id),
          value_labels: { match: "Counts", other: "Doesn't count" },
        }}
        capabilities={capabilities}
        options={options}
        onChange={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: "Customize match labels" }).checked,
    ).toBe(false);
    expect(screen.queryByLabelText("Show matches as")).toBeNull();
  });
  it("makes evaluation output type an explicit capability-bound choice", () => {
    render(<Harness />);
    choose("Choose a source", "Quality");
    choose("Evaluation output for value_1", "boolean");
    expect(wire().inputs[0]).toMatchObject({
      source: "eval",
      config_id: id,
      config_hash: "a".repeat(64),
      value_type: "boolean",
      reducer: "latest",
    });
    expect(wire().field_spec.conditions[0].value).toBe(true);
    choose("Evaluation output for value_1", "text");
    expect(wire().inputs[0].value_type).toBe("text");
    expect(wire().field_spec.conditions[0].value).toBeNull();
  });
  it("reads each condition as one sentence beside the fields that edit it", () => {
    render(<Harness />);
    choose("Choose a source", "Resolved");
    const first = screen.getByRole("group", { name: "Condition 1" });
    expect(first).toHaveAccessibleDescription(
      "Where resolved (latest) is true",
    );
    // Still a form: every part of the sentence keeps its own labelled field.
    for (const name of [
      "Source 1",
      "When a conversation has several values, use",
      "Comparison",
      "Value for resolved",
    ])
      expect(within(first).getByRole("combobox", { name })).toBeTruthy();
    choose("Value for resolved", "False");
    expect(first).toHaveAccessibleDescription(
      "Where resolved (latest) is false",
    );
    fireEvent.click(screen.getByRole("button", { name: "Add condition" }));
    choose("Choose a source", "Locale");
    const second = screen.getByRole("group", { name: "Condition 2" });
    // A value not chosen yet stays visibly missing ("…" on screen), never a
    // default, and a screen reader hears words instead of an ellipsis.
    expect(second).toHaveAccessibleDescription(
      "And locale (latest) is no value yet",
    );
    expect(within(second).getByText("…")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    choose("Value for locale", "fr");
    expect(second).toHaveAccessibleDescription('And locale (latest) is "fr"');
    expect(wire().field_spec.conditions).toEqual([
      { alias: "value_1", operator: "eq", value: false },
      { alias: "value_2", operator: "eq", value: "fr" },
    ]);
  });
  it("names sources in the condition sentence the way the preview does", () => {
    render(<Harness sourceLabels={{ value_1: "Resolved" }} />);
    choose("Choose a source", "Resolved");
    expect(
      screen.getByRole("group", { name: "Condition 1" }),
    ).toHaveAccessibleDescription("Where Resolved (latest) is true");
  });
});
