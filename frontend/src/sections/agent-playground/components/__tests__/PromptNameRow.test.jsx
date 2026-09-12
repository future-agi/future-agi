/* eslint-disable react/prop-types */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  FormProvider,
  useForm,
  useFormContext,
  useWatch,
} from "react-hook-form";
import PromptNameRow from "../PromptNameRow";

const mockVersions = [
  {
    id: "prompt-version-1",
    template_version: "v1",
    is_draft: false,
    prompt_config_snapshot: {
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "Use {{topic}}" }],
        },
      ],
      configuration: {
        model: "gpt-4o-mini",
        response_format: "text",
        output_format: "string",
        template_format: "mustache",
      },
    },
  },
  {
    id: "prompt-version-2",
    template_version: "v2",
    is_draft: false,
    prompt_config_snapshot: {
      messages: [
        {
          role: "user",
          content: [{ type: "text", text: "Use {{ topic }}" }],
        },
      ],
      configuration: {
        model: "gpt-4o-mini",
        response_format: "text",
        output_format: "string",
        template_format: "jinja",
      },
    },
  },
];

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetPromptVersionsInfinite: () => ({
    data: {
      pages: [
        {
          data: {
            results: mockVersions,
          },
        },
      ],
    },
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
  useGetPromptVersionDetail: () => ({ data: null }),
}));

vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: function MockFormTextFieldV2({ fieldName, label }) {
    const { register } = useFormContext();
    return <input aria-label={label} {...register(fieldName)} />;
  },
}));

vi.mock("src/components/FormSelectField/FormSelectField", () => ({
  EnhancedFormSelectField: function MockEnhancedFormSelectField({
    fieldName,
    options,
  }) {
    const { setValue } = useFormContext();
    const value = useWatch({ name: fieldName });

    return (
      <select
        aria-label={fieldName}
        value={value || ""}
        onChange={(event) =>
          setValue(fieldName, event.target.value, { shouldDirty: true })
        }
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  },
}));

vi.mock("src/sections/workbench/createPrompt/SharedStyledComponents", () => ({
  DraftBadge: function MockDraftBadge({ children }) {
    return <span>{children}</span>;
  },
}));

function FormValuesProbe() {
  const values = useWatch();
  return (
    <>
      <div data-testid="template-format">{values.templateFormat}</div>
      <div data-testid="payload-template-format">
        {values.payload?.promptConfig?.[0]?.configuration?.template_format}
      </div>
    </>
  );
}

function renderPromptNameRow() {
  function Wrapper() {
    const methods = useForm({
      defaultValues: {
        name: "saved_prompt",
        prompt_template_id: "prompt-template-1",
        prompt_version_id: "prompt-version-1",
        outputFormat: "string",
        templateFormat: "mustache",
        modelConfig: {
          model: "gpt-4o-mini",
          responseFormat: "text",
        },
        messages: [],
        payload: null,
      },
    });

    return (
      <FormProvider {...methods}>
        <PromptNameRow control={methods.control} />
        <FormValuesProbe />
      </FormProvider>
    );
  }

  return render(<Wrapper />);
}

describe("PromptNameRow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("updates templateFormat when switching saved prompt versions", async () => {
    renderPromptNameRow();

    expect(screen.getByTestId("template-format")).toHaveTextContent("mustache");

    fireEvent.change(screen.getByLabelText("prompt_version_id"), {
      target: { value: "prompt-version-2" },
    });

    await waitFor(() =>
      expect(screen.getByTestId("template-format")).toHaveTextContent("jinja"),
    );
    expect(screen.getByTestId("payload-template-format")).toHaveTextContent(
      "jinja",
    );
  });
});
