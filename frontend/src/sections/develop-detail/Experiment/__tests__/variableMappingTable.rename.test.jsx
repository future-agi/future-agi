// A prompt or agent run fills {{name}} only from the column named `name`.
// On the Data step such a variable says at its row what to do (rename it in
// the prompt, or add the column) instead of reading as mapped; the column
// picker stays. Endpoint runs keep any mapping.
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import VariableMappingTable from "../VariableMappingTable";
import { ExperimentFormContext } from "../ExperimentFormContext";

const COLUMNS = [
  { id: "c-input", name: "input", data_type: "text" },
  { id: "c-expected", name: "expected_output", data_type: "text" },
];

function Harness({ variables, mapping, nameOnly, kind }) {
  const form = useForm({
    defaultValues: {
      promptConfig: [{ variables, variableMapping: mapping }],
    },
  });
  return (
    <MemoryRouter>
      <ExperimentFormContext.Provider
        value={{
          mappingColumns: COLUMNS,
          jsonSchemas: {},
          sampleRow: { row_id: "r1", "c-input": { cell_value: "France" } },
          sampleRowLabel: "Row 1",
        }}
      >
        <VariableMappingTable
          control={form.control}
          setValue={form.setValue}
          fieldPrefix="promptConfig.0"
          title="Untitled-1 v1"
          nameOnly={nameOnly}
          kind={kind}
        />
      </ExperimentFormContext.Provider>
    </MemoryRouter>
  );
}
Harness.propTypes = {
  variables: PropTypes.arrayOf(PropTypes.string),
  mapping: PropTypes.object,
  nameOnly: PropTypes.bool,
  kind: PropTypes.string,
};

afterEach(cleanup);

describe("VariableMappingTable · a prompt variable not named like a column", () => {
  it("says at the variable what to do and keeps the column picker", () => {
    render(
      <Harness
        variables={["name"]}
        mapping={{ name: "c-input" }}
        nameOnly
        kind="prompt"
      />,
    );
    expect(screen.getByTestId("rename-name").textContent).toContain(
      "Experiments fill {{name}} only from a column named “name”. Rename {{name}} to {{input}} in the prompt, or add a column named “name” to the dataset.",
    );
    expect(screen.getByTestId("mapping-chip").textContent).toBe(
      "Rename needed",
    );
    expect(
      within(screen.getByTestId("column-select-name")).getByRole("combobox"),
    ).toBeTruthy();
  });

  it("an unmapped variable gets the same words; its own line stays", () => {
    render(
      <Harness
        variables={["name"]}
        mapping={{ name: "" }}
        nameOnly
        kind="prompt"
      />,
    );
    expect(screen.getByTestId("rename-name").textContent).toContain(
      "Rename {{name}} to a column name (input, expected_output) in the prompt, or add a column named “name” to the dataset.",
    );
    expect(screen.getByTestId("unmapped-message")).toBeTruthy();
  });

  it("an agent run says the agent", () => {
    render(
      <Harness
        variables={["name"]}
        mapping={{ name: "c-input" }}
        nameOnly
        kind="agent"
      />,
    );
    expect(screen.getByTestId("rename-name").textContent).toContain(
      "Rename {{name}} to {{input}} in the agent,",
    );
  });

  it("a variable named like its column reads as mapped", () => {
    render(
      <Harness
        variables={["input"]}
        mapping={{ input: "c-input" }}
        nameOnly
        kind="prompt"
      />,
    );
    expect(screen.queryByTestId("rename-input")).toBeNull();
    expect(screen.getByTestId("mapping-chip").textContent).toBe("All mapped");
  });

  it("an endpoint run keeps any mapping", () => {
    render(
      <Harness
        variables={["name"]}
        mapping={{ name: "c-input" }}
        nameOnly={false}
        kind="http"
      />,
    );
    expect(screen.queryByTestId("rename-name")).toBeNull();
    expect(screen.getByTestId("mapping-chip").textContent).toBe("All mapped");
  });
});

describe("DataStep turns it on for prompt and agent runs on the real backend", () => {
  const SRC = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "..",
      "StepperComponentExperiment",
      "DataStep.jsx",
    ),
    "utf8",
  );

  it("passes nameOnly outside prototype mode for prompt and agent runs only", () => {
    expect(SRC).toMatch(
      /const nameOnly =\s*!isPrototypeMode\(\) &&\s*\(type === PROMPT_CONFIG_TYPE\.PROMPT \|\|\s*type === PROMPT_CONFIG_TYPE\.AGENT\);/,
    );
    expect(SRC).toMatch(
      /<VariableMappingTable[\s\S]*?nameOnly=\{nameOnly\}\s*kind=\{type\}/,
    );
  });
});
