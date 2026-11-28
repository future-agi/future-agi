import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import { QueryInput } from "../QueryInput";

const FIELDS = [
  {
    value: "status",
    label: "Status",
    type: "enum",
    choices: ["OK", "ERROR"],
  },
  { value: "model", label: "Model", type: "string" },
  { value: "provider", label: "Provider", type: "string" },
];

const FIELD_MAP = Object.fromEntries(FIELDS.map((f) => [f.value, f]));

const TOKENS = [
  { field: "status", operator: "is", value: "ERROR" },
  { field: "model", operator: "contains", value: "gpt" },
  { field: "provider", operator: "contains", value: "openai" },
];

const renderWithTokens = (initialTokens) =>
  render(
    <QueryInput
      filterFields={FIELDS}
      fieldMap={FIELD_MAP}
      onApply={vi.fn()}
      initialTokens={initialTokens}
    />,
  );

describe("QueryInput operator label", () => {
  it.each([
    ["no tokens", []],
    ["one token", TOKENS.slice(0, 1)],
  ])("shows no AND label with %s", (_, initialTokens) => {
    const utils = renderWithTokens(initialTokens);

    expect(utils.queryByText("AND")).not.toBeInTheDocument();
  });

  it("shows one static AND label between two query tokens", () => {
    const utils = renderWithTokens(TOKENS.slice(0, 2));

    const labels = utils.getAllByText("AND");
    expect(labels).toHaveLength(1);

    const [label] = labels;
    expect(label.previousElementSibling).toHaveTextContent("Status Is ERROR");
    expect(label.nextElementSibling).toHaveTextContent("Model Contains gpt");

    expect(label).not.toHaveAttribute("role");
    expect(label).not.toHaveAttribute("tabindex");
    expect(label.closest('[role="button"]')).toBeNull();
  });

  it("shows an AND label between each adjacent pair of tokens", () => {
    const utils = renderWithTokens(TOKENS);

    const labels = utils.getAllByText("AND");
    expect(labels).toHaveLength(2);
    expect(labels[1].previousElementSibling).toHaveTextContent(
      "Model Contains gpt",
    );
    expect(labels[1].nextElementSibling).toHaveTextContent(
      "Provider Contains openai",
    );
  });
});
