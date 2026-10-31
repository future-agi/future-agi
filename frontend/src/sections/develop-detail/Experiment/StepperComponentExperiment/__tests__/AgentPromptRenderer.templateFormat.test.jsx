import React from "react";
import PropTypes from "prop-types";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useController, useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentPromptRenderer from "../AgentPromptRenderer";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {},
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span />,
}));

const versions = [
  {
    id: "v-jinja",
    template_version: "v2",
    variable_names: {},
    prompt_config_snapshot: {
      messages: [],
      configuration: { template_format: "jinja" },
    },
  },
  {
    id: "v-old",
    template_version: "v1",
    variable_names: {},
    prompt_config_snapshot: { messages: [], configuration: {} },
  },
];

// Stable query results, like react-query's, so effects keyed on them settle.
const versionsQuery = {
  data: { pages: [{ results: versions }] },
  isPending: false,
  refetch: () => {},
  fetchNextPage: () => {},
  hasNextPage: false,
  isFetchingNextPage: false,
};
const agentVersionsQuery = {
  data: undefined,
  isPending: false,
  fetchNextPage: () => {},
  hasNextPage: false,
  isFetchingNextPage: false,
};

vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => versionsQuery,
}));

vi.mock("src/api/experiment/use-get-agents.js", () => ({
  useGetAgentVersions: () => agentVersionsQuery,
}));

vi.mock("../NewModelRenderWithParamsTool", () => ({ default: () => null }));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({ default: () => null }),
);
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));

// Plain <select> bound to the form field, standing in for the version picker.
vi.mock(
  "src/components/searchable-select-control/SearchableSelectControl",
  () => ({
    default: function VersionSelect({
      control,
      fieldName,
      options,
      getOptionValue,
    }) {
      const { field } = useController({ control, name: fieldName });
      return (
        <select
          aria-label="version"
          value={field.value ?? ""}
          onChange={(e) => field.onChange(e.target.value)}
        >
          <option value="" />
          {options.map((o) => (
            <option key={getOptionValue(o)} value={getOptionValue(o)}>
              {o.template_version}
            </option>
          ))}
        </select>
      );
    },
  }),
);

let form;

function Harness({ promptVersion = null, configuration }) {
  const methods = useForm({
    defaultValues: {
      promptConfig: [
        {
          experimentType: "llm",
          promptId: "prompt-1",
          promptVersion,
          name: "My prompt",
          model: [{ id: "m1", value: "gpt-4o" }],
          configuration: configuration ?? { toolChoice: "auto", tools: [] },
          modelParams: {},
          outputFormat: "string",
        },
      ],
    },
  });
  form = methods;
  return (
    <AgentPromptRenderer
      prompt={methods.getValues("promptConfig.0")}
      control={methods.control}
      setValue={methods.setValue}
      getValues={methods.getValues}
      watch={methods.watch}
      unregister={methods.unregister}
      index={0}
      errors={{}}
      allColumns={[]}
      origin="run-experiment"
      type="prompt"
      fieldPrefix="promptConfig.0"
    />
  );
}

Harness.propTypes = {
  promptVersion: PropTypes.string,
  configuration: PropTypes.object,
};

const formatField = "promptConfig.0.configuration.template_format";

const pickFormat = (current, label) => {
  fireEvent.click(
    screen.getByRole("button", { name: new RegExp(current), hidden: true }),
  );
  fireEvent.click(screen.getByRole("menuitem", { name: new RegExp(label) }));
};

describe("AgentPromptRenderer template format", () => {
  beforeEach(() => {
    form = null;
  });

  it("defaults to the format the selected version was saved with", () => {
    render(<Harness />);
    // The first version is auto-selected and was saved as Jinja.
    expect(form.getValues("promptConfig.0.promptVersion")).toBe("v-jinja");
    expect(
      screen.getByRole("button", { name: /Jinja/, hidden: true }),
    ).toBeInTheDocument();
    // Untouched: nothing is written, so the backend reads the version.
    expect(form.getValues(formatField)).toBeUndefined();
  });

  it("falls back to Mustache for versions saved without a format", () => {
    render(<Harness promptVersion="v-old" />);
    expect(
      screen.getByRole("button", { name: /Mustache/, hidden: true }),
    ).toBeInTheDocument();
  });

  it("writes an explicit pick into the experiment configuration", () => {
    render(<Harness />);
    pickFormat("Jinja", "Mustache");
    expect(form.getValues(formatField)).toBe("mustache");
    expect(
      screen.getByRole("button", { name: /Mustache/, hidden: true }),
    ).toBeInTheDocument();
  });

  it("drops an explicit pick when another version is selected", () => {
    render(<Harness promptVersion="v-old" />);
    pickFormat("Mustache", "Jinja");
    expect(form.getValues(formatField)).toBe("jinja");

    act(() => {
      fireEvent.change(screen.getByLabelText("version"), {
        target: { value: "v-jinja" },
      });
    });
    expect(form.getValues(formatField)).toBeUndefined();
    expect(
      screen.getByRole("button", { name: /Jinja/, hidden: true }),
    ).toBeInTheDocument();
  });

  it("keeps a saved explicit format when an experiment is edited", () => {
    render(
      <Harness
        promptVersion="v-jinja"
        configuration={{
          toolChoice: "auto",
          tools: [],
          template_format: "mustache",
        }}
      />,
    );
    expect(form.getValues(formatField)).toBe("mustache");
    expect(
      screen.getByRole("button", { name: /Mustache/, hidden: true }),
    ).toBeInTheDocument();
  });
});
