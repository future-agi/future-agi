/* eslint-disable react/prop-types */
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useForm } from "react-hook-form";
import AgentPromptRenderer from "../AgentPromptRenderer";

// ---- Mocks ----

const promptVersion = {
  id: "v1",
  version: "v1.0",
  status: "published",
  variable_names: { question: "" },
};

vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => ({
    data: { pages: [{ results: [promptVersion] }] },
    isPending: false,
    refetch: vi.fn(),
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
}));

vi.mock("src/api/experiment/use-get-agents.js", () => ({
  useGetAgentVersions: () => ({
    data: undefined,
    isPending: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
}));

// Heavy children that are irrelevant to the mapping behaviour.
vi.mock("../NewModelRenderWithParamsTool", () => ({ default: () => null }));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({
    default: () => null,
  }),
);
vi.mock(
  "src/components/searchable-select-control/SearchableSelectControl",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));

// Stand-in for the column picker: exposes one button per option that writes
// the chosen column into the same form field the real control writes to.
vi.mock("src/sections/develop-detail/Common/FieldSelection", () => ({
  default: ({ field, fieldName, control, allColumns }) => (
    <div data-testid={`picker-${field}`}>
      {allColumns.map((col) => (
        <button
          key={col.field}
          type="button"
          onClick={() =>
            control._formValues &&
            control.setValue?.(fieldName, col.field, { shouldValidate: false })
          }
        >
          {`map ${field} to ${col.headerName}`}
        </button>
      ))}
    </div>
  ),
}));

// ---- Helpers ----

function Harness({ allColumns, onState }) {
  const { control, setValue, getValues, watch, unregister } = useForm({
    defaultValues: {
      config: { promptVersion: "v1", model: [], variableMapping: {} },
    },
  });
  control.setValue = setValue;
  onState({ getValues });
  return (
    <AgentPromptRenderer
      prompt={{ name: "P", promptId: "p1" }}
      onRemove={vi.fn()}
      setValue={setValue}
      control={control}
      index={0}
      getValues={getValues}
      watch={watch}
      unregister={unregister}
      allColumns={allColumns}
      errors={{}}
      fieldPrefix="config"
      type="prompt"
    />
  );
}

function renderWith(allColumns) {
  const state = {};
  render(
    <Harness
      allColumns={allColumns}
      onState={(s) => Object.assign(state, s)}
    />,
  );
  return state;
}

// ---- Tests ----

describe("AgentPromptRenderer variable mapping", () => {
  it("seeds the mapping when a column name matches the variable exactly", async () => {
    const state = renderWith([
      { headerName: "question", field: "col_question" },
    ]);

    await waitFor(() => {
      expect(state.getValues("config.variableMapping")).toEqual({
        question: "col_question",
      });
      expect(state.getValues("config.unmappedVariables")).toBe(0);
    });
  });

  it("leaves a case-different column unmapped instead of matching it", async () => {
    const state = renderWith([
      { headerName: "Question", field: "col_question" },
    ]);

    await waitFor(() => {
      expect(state.getValues("config.unmappedVariables")).toBe(1);
    });
    expect(state.getValues("config.variableMapping")).toEqual({});
    expect(
      screen.getByText(/1 variables mapped to dataset columns/),
    ).toBeTruthy();
  });

  it("clears the run gate once the variable is mapped by hand", async () => {
    const user = userEvent.setup();
    const state = renderWith([
      { headerName: "Question", field: "col_question" },
    ]);

    await waitFor(() => {
      expect(state.getValues("config.unmappedVariables")).toBe(1);
    });

    await user.click(screen.getByText("map question to Question"));

    await waitFor(() => {
      expect(state.getValues("config.variableMapping")).toEqual({
        question: "col_question",
      });
      expect(state.getValues("config.unmappedVariables")).toBe(0);
    });
  });
});
