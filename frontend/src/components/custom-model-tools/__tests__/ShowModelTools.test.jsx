/* eslint-disable react/prop-types */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ShowModelTools from "../ShowModelTools";

const hookState = vi.hoisted(() => ({ value: { data: undefined, isLoading: false } }));

// The component reads runPromptOptions?.available_tools (snake_case from the API).
vi.mock("src/api/develop/develop-detail", () => ({
  useRunPromptOptions: () => hookState.value,
}));

vi.mock("./EditTool", () => ({ default: () => null }));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldState: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => <span /> }));
vi.mock("src/components/iconify", () => ({ default: () => <span /> }));

const runPromptOptions = {
  available_tools: [
    { id: "t1", name: "Tool Alpha", config_type: "json" },
    { id: "t2", name: "Tool Beta", config_type: "json" },
  ],
  tool_choices: [],
  output_formats: [],
  models: [],
};

describe("ShowModelTools (TH-5930 tools list)", () => {
  beforeEach(() => {
    hookState.value = { data: runPromptOptions, isLoading: false };
  });

  it("renders the selected tools from the snake_case available_tools response", () => {
    render(
      <ShowModelTools
        open
        onClose={vi.fn()}
        handleApply={vi.fn()}
        tools={[{ id: "t1" }]}
      />,
    );

    // Reading availableTools (camelCase) would leave this list empty.
    expect(screen.getByText("Tool Alpha")).toBeInTheDocument();
    // Only the selected tool is listed.
    expect(screen.queryByText("Tool Beta")).not.toBeInTheDocument();
  });
});
