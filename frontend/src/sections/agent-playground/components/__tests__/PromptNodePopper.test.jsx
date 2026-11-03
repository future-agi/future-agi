import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PromptNodePopper from "../PromptNodePopper";
import {
  useGetLibraryTemplatesInfinite,
  useGetNodeTemplates,
  useGetPromptTemplatesInfinite,
} from "src/api/agent-playground/agent-playground";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";

const mockAddNode = vi.fn();

vi.mock("../../AgentBuilder/hooks/useAddNodeOptimistic", () => ({
  default: () => ({ addNode: mockAddNode }),
}));

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetLibraryTemplatesInfinite: vi.fn(),
  useGetNodeTemplates: vi.fn(),
  useGetPromptTemplatesInfinite: vi.fn(),
}));

vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src }) => <span data-testid="svg-color">{src}</span>,
}));

vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: ({ onChange, placeholder, searchQuery }) => (
    <input
      aria-label={placeholder}
      onChange={onChange}
      placeholder={placeholder}
      value={searchQuery}
    />
  ),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        getPromptVersions: vi.fn(() => "/model-hub/prompt-history-executions/"),
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

const anchorEl = document.createElement("button");
const nodeTemplates = [
  {
    id: "llm_prompt",
    node_template_id: "node-template-llm-prompt",
  },
];
const responseSchema = {
  type: "object",
  properties: {
    answer: { type: "string" },
  },
  required: ["answer"],
};

const savedPrompt = {
  id: "saved-template-1",
  name: "Saved Support Prompt",
};

const libraryTemplate = {
  id: "library-template-1",
  name: "Library Research Template",
  prompt_config_snapshot: {
    messages: [
      {
        role: "system",
        content: [{ type: "text", text: "Be concise." }],
      },
      {
        role: "user",
        content: [{ type: "text", text: "Summarize {{topic}}." }],
      },
    ],
    configuration: {
      model: "gpt-4o-mini",
      model_detail: { model_name: "gpt-4o-mini", providers: "openai" },
      response_format: "text",
      output_format: "string",
      template_format: "jinja",
      temperature: 0.2,
    },
  },
};

const remoteMediaLibraryTemplate = {
  ...libraryTemplate,
  id: "library-template-remote-media",
  name: "Library Remote Media Template",
  prompt_config_snapshot: {
    messages: [
      {
        role: "user",
        content: [
          "Describe the attached context.",
          {
            type: "image_url",
            image_url: "https://example.com/image.png",
          },
          {
            type: "pdf_url",
            pdf_url: "https://example.com/doc.pdf",
          },
          {
            type: "audio_url",
            audio_url: "https://example.com/audio.mp3",
          },
        ],
      },
    ],
    configuration: null,
  },
};

const legacyListLibraryTemplate = {
  id: "library-template-legacy-list",
  name: "Legacy List Library Template",
  prompt_config_snapshot: [
    {
      messages: [{ role: "user", content: "Hello {{name}}" }],
      model: "gpt-4o",
      model_detail: { model_name: "gpt-4o", providers: "openai" },
      response_format: "json_object",
      output_format: "json",
      template_format: "mustache",
      temperature: 0.4,
    },
  ],
};

const toolConfiguredLibraryTemplate = {
  ...libraryTemplate,
  id: "library-template-tools",
  name: "Library Template With Tools",
  prompt_config_snapshot: {
    ...libraryTemplate.prompt_config_snapshot,
    configuration: {
      ...libraryTemplate.prompt_config_snapshot.configuration,
      tools: [{ name: "external_lookup" }],
      tool_choice: "required",
    },
  },
};

const promptVersion = {
  id: "prompt-version-1",
  is_default: true,
  prompt_config_snapshot: {
    messages: [
      {
        role: "system",
        content: [{ type: "text", text: "Saved system" }],
      },
      {
        role: "user",
        content: [{ type: "text", text: "Saved user" }],
      },
    ],
    configuration: {
      model: "gpt-4o",
      model_detail: { model_name: "gpt-4o", providers: "openai" },
      response_format: "text",
      output_format: "string",
    },
  },
};

function infiniteResult(items, overrides = {}, shape = "saved") {
  const pageData =
    shape === "library"
      ? {
          result: {
            data: items,
            page_number: 1,
            total_count: items.length,
          },
        }
      : { results: items };

  return {
    data: {
      pages: [{ data: pageData }],
    },
    isLoading: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    isError: false,
    ...overrides,
  };
}

function savedPromptsResult(items, overrides = {}) {
  return infiniteResult(items, overrides, "saved");
}

function libraryTemplatesResult(items, overrides = {}) {
  return infiniteResult(items, overrides, "library");
}

function renderPopper(props = {}) {
  return render(
    <PromptNodePopper open anchorEl={anchorEl} onClose={vi.fn()} {...props} />,
  );
}

function scrollListTo({ scrollTop, clientHeight, scrollHeight }) {
  const list = screen.getByRole("list");
  Object.defineProperties(list, {
    scrollTop: { configurable: true, value: scrollTop },
    clientHeight: { configurable: true, value: clientHeight },
    scrollHeight: { configurable: true, value: scrollHeight },
  });

  fireEvent.scroll(list);
}

function scrollListToBottom() {
  scrollListTo({ scrollTop: 95, clientHeight: 10, scrollHeight: 100 });
}

describe("PromptNodePopper", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGetNodeTemplates.mockReturnValue({ data: nodeTemplates });
    useGetPromptTemplatesInfinite.mockReturnValue(savedPromptsResult([]));
    useGetLibraryTemplatesInfinite.mockReturnValue(libraryTemplatesResult([]));
    axios.get.mockResolvedValue({ data: { results: [promptVersion] } });
  });

  it("renders saved prompts and library templates as separate non-empty sections", () => {
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt]),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate]),
    );

    renderPopper();

    expect(screen.getByText("My Prompts")).toBeInTheDocument();
    expect(screen.getByText(savedPrompt.name)).toBeInTheDocument();
    expect(screen.getByText("Library Templates")).toBeInTheDocument();
    expect(screen.getByText(libraryTemplate.name)).toBeInTheDocument();
  });

  it("hides the saved section when only library templates are present", () => {
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate]),
    );

    renderPopper();

    expect(screen.queryByText("My Prompts")).not.toBeInTheDocument();
    expect(screen.getByText("Library Templates")).toBeInTheDocument();
    expect(screen.queryByText("No prompts found")).not.toBeInTheDocument();
  });

  it("hides the library section when only saved prompts are present", () => {
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt]),
    );

    renderPopper();

    expect(screen.getByText("My Prompts")).toBeInTheDocument();
    expect(screen.queryByText("Library Templates")).not.toBeInTheDocument();
    expect(screen.queryByText("No prompts found")).not.toBeInTheDocument();
  });

  it("shows an empty state when no saved prompts or library templates match", () => {
    renderPopper();

    expect(screen.getByText("No prompts found")).toBeInTheDocument();
    expect(screen.queryByText("My Prompts")).not.toBeInTheDocument();
    expect(screen.queryByText("Library Templates")).not.toBeInTheDocument();
  });

  it("passes the same search query to saved prompts and library templates", async () => {
    renderPopper();

    fireEvent.change(screen.getByLabelText("Search prompts..."), {
      target: { value: "research" },
    });

    await waitFor(() => {
      expect(useGetPromptTemplatesInfinite).toHaveBeenLastCalledWith(
        "research",
        { enabled: true },
      );
      expect(useGetLibraryTemplatesInfinite).toHaveBeenLastCalledWith(
        "research",
        { enabled: true },
      );
    });
  });

  it("seeds an LLM prompt from a library template without fetching versions", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(libraryTemplate.name));

    expect(axios.get).not.toHaveBeenCalled();
    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        name: libraryTemplate.name,
        prompt_template_id: null,
        prompt_version_id: null,
        outputFormat: "string",
        templateFormat: "jinja",
        modelConfig: expect.objectContaining({ model: "gpt-4o-mini" }),
        payload: expect.objectContaining({
          promptConfig: [
            expect.objectContaining({
              configuration: expect.objectContaining({
                template_format: "jinja",
              }),
            }),
          ],
        }),
        messages: expect.arrayContaining([
          expect.objectContaining({
            role: "user",
            content: [{ type: "text", text: "Summarize {{topic}}." }],
          }),
        ]),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("rejects library templates with remote media URL blocks", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([remoteMediaLibraryTemplate]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(remoteMediaLibraryTemplate.name));

    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This library template can't be added because Agent Builder currently supports text-only library prompt templates with string or JSON outputs.",
      { variant: "error" },
    );
  });

  it("rejects library templates with unsupported output formats before inserting a node", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([
        {
          ...libraryTemplate,
          id: "image-output-library-template",
          name: "Image Output Library Template",
          prompt_config_snapshot: {
            ...libraryTemplate.prompt_config_snapshot,
            configuration: {
              ...libraryTemplate.prompt_config_snapshot.configuration,
              output_format: "image",
            },
          },
        },
      ]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText("Image Output Library Template"));

    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This library template can't be added because Agent Builder currently supports text-only library prompt templates with string or JSON outputs.",
      { variant: "error" },
    );
  });

  it("rejects incompatible library template snapshots without adding a node", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([
        {
          id: "bad-library-template",
          name: "Unsupported Library Template",
          prompt_config_snapshot: {
            messages: [
              { role: "tool", content: [{ type: "image", url: "x" }] },
            ],
            configuration: {},
          },
        },
      ]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText("Unsupported Library Template"));

    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This library template can't be added because Agent Builder currently supports text-only library prompt templates with string or JSON outputs.",
      { variant: "error" },
    );
  });

  it("seeds library templates with structured output formats", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([
        {
          ...libraryTemplate,
          id: "json-library-template",
          name: "JSON Output Library Template",
          prompt_config_snapshot: {
            ...libraryTemplate.prompt_config_snapshot,
            configuration: {
              ...libraryTemplate.prompt_config_snapshot.configuration,
              response_format: "json_schema",
              response_schema: responseSchema,
              output_format: "json",
            },
          },
        },
      ]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText("JSON Output Library Template"));

    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        outputFormat: "json",
        name: "JSON Output Library Template",
        modelConfig: expect.objectContaining({
          responseFormat: "json_schema",
          responseSchema,
        }),
        payload: expect.objectContaining({
          promptConfig: [
            expect.objectContaining({
              configuration: expect.objectContaining({
                response_schema: responseSchema,
              }),
            }),
          ],
        }),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("normalizes legacy list-shaped library snapshots before inserting", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([legacyListLibraryTemplate]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(legacyListLibraryTemplate.name));

    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        name: legacyListLibraryTemplate.name,
        outputFormat: "json",
        templateFormat: "mustache",
        modelConfig: expect.objectContaining({
          model: "gpt-4o",
          responseFormat: "json_object",
        }),
        messages: expect.arrayContaining([
          expect.objectContaining({
            role: "user",
            content: [{ type: "text", text: "Hello {{name}}" }],
          }),
        ]),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("strips active tool configuration from shared library templates", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([toolConfiguredLibraryTemplate]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(toolConfiguredLibraryTemplate.name));

    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        name: toolConfiguredLibraryTemplate.name,
        modelConfig: expect.objectContaining({
          tools: [],
          toolChoice: "auto",
        }),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps saved prompt selection on the version-fetch path", async () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(savedPrompt.name));

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledWith(
        endpoints.develop.runPrompt.getPromptVersions(),
        {
          params: { template_id: savedPrompt.id, modality: "chat" },
        },
      );
    });
    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        name: savedPrompt.name,
        prompt_template_id: savedPrompt.id,
        prompt_version_id: promptVersion.id,
        modelConfig: expect.objectContaining({ model: "gpt-4o" }),
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("fetches the next saved and library pages when both have more results", () => {
    const fetchNextSavedPage = vi.fn();
    const fetchNextLibraryPage = vi.fn();
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt], {
        fetchNextPage: fetchNextSavedPage,
        hasNextPage: true,
      }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate], {
        fetchNextPage: fetchNextLibraryPage,
        hasNextPage: true,
      }),
    );

    renderPopper();
    scrollListToBottom();

    expect(fetchNextSavedPage).toHaveBeenCalledTimes(1);
    expect(fetchNextLibraryPage).toHaveBeenCalledTimes(1);
  });

  it("fetches only the saved prompt page when library templates have no next page", () => {
    const fetchNextSavedPage = vi.fn();
    const fetchNextLibraryPage = vi.fn();
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt], {
        fetchNextPage: fetchNextSavedPage,
        hasNextPage: true,
      }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate], {
        fetchNextPage: fetchNextLibraryPage,
        hasNextPage: false,
      }),
    );

    renderPopper();
    scrollListToBottom();

    expect(fetchNextSavedPage).toHaveBeenCalledTimes(1);
    expect(fetchNextLibraryPage).not.toHaveBeenCalled();
  });

  it("fetches only the library template page when saved prompts have no next page", () => {
    const fetchNextSavedPage = vi.fn();
    const fetchNextLibraryPage = vi.fn();
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt], {
        fetchNextPage: fetchNextSavedPage,
        hasNextPage: false,
      }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate], {
        fetchNextPage: fetchNextLibraryPage,
        hasNextPage: true,
      }),
    );

    renderPopper();
    scrollListToBottom();

    expect(fetchNextSavedPage).not.toHaveBeenCalled();
    expect(fetchNextLibraryPage).toHaveBeenCalledTimes(1);
  });

  it("does not fetch either source when the list is not scrolled to the bottom", () => {
    const fetchNextSavedPage = vi.fn();
    const fetchNextLibraryPage = vi.fn();
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([savedPrompt], {
        fetchNextPage: fetchNextSavedPage,
        hasNextPage: true,
      }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate], {
        fetchNextPage: fetchNextLibraryPage,
        hasNextPage: true,
      }),
    );

    renderPopper();
    scrollListTo({ scrollTop: 20, clientHeight: 10, scrollHeight: 100 });

    expect(fetchNextSavedPage).not.toHaveBeenCalled();
    expect(fetchNextLibraryPage).not.toHaveBeenCalled();
  });

  it("shows an error state when a prompt-template query fails", () => {
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([], { isError: true }),
    );

    renderPopper();

    expect(
      screen.getByText(
        "Unable to load library templates. Your prompts may still be available.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("No prompts found")).not.toBeInTheDocument();
  });

  it("shows a saved-prompt error while library templates remain available", () => {
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([], { isError: true }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([libraryTemplate]),
    );

    renderPopper();

    expect(screen.getByText(libraryTemplate.name)).toBeInTheDocument();
    expect(
      screen.getByText(
        "Unable to load your prompts. Library templates may still be available.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("No prompts found")).not.toBeInTheDocument();
  });

  it("shows a combined error when saved prompts and library templates both fail", () => {
    useGetPromptTemplatesInfinite.mockReturnValue(
      savedPromptsResult([], { isError: true }),
    );
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([], { isError: true }),
    );

    renderPopper();

    expect(
      screen.getByText(
        "Unable to load prompt templates. Check your connection and try again.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("No prompts found")).not.toBeInTheDocument();
  });
});
