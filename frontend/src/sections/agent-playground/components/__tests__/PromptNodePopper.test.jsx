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

const multimodalLibraryTemplate = {
  ...libraryTemplate,
  id: "library-template-multimodal",
  name: "Library Multimodal Template",
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

  it("seeds an LLM prompt from a multimodal library template", () => {
    const onClose = vi.fn();
    const onNodeSelect = vi.fn();
    useGetLibraryTemplatesInfinite.mockReturnValue(
      libraryTemplatesResult([multimodalLibraryTemplate]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText(multimodalLibraryTemplate.name));

    expect(onNodeSelect).toHaveBeenCalledWith(
      "llm_prompt",
      "node-template-llm-prompt",
      expect.objectContaining({
        name: multimodalLibraryTemplate.name,
        prompt_template_id: null,
        prompt_version_id: null,
        modelConfig: expect.objectContaining({ model: "" }),
        messages: expect.arrayContaining([
          expect.objectContaining({
            role: "user",
            content: [
              { type: "text", text: "Describe the attached context." },
              {
                type: "image_url",
                image_url: { url: "https://example.com/image.png" },
              },
              {
                type: "pdf_url",
                pdf_url: { url: "https://example.com/doc.pdf" },
              },
              {
                type: "audio_url",
                audio_url: { url: "https://example.com/audio.mp3" },
              },
            ],
          }),
        ]),
      }),
    );
    expect(onClose).toHaveBeenCalled();
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
      "This library template can't be added because its prompt configuration isn't compatible with LLM prompt nodes.",
      { variant: "error" },
    );
  });

  it("rejects library templates with unsupported output formats", () => {
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
              output_format: "json",
            },
          },
        },
      ]),
    );

    renderPopper({ onClose, onNodeSelect });

    fireEvent.click(screen.getByText("JSON Output Library Template"));

    expect(onNodeSelect).not.toHaveBeenCalled();
    expect(mockAddNode).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "This library template can't be added because its prompt configuration isn't compatible with LLM prompt nodes.",
      { variant: "error" },
    );
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
});
