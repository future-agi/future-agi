import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useCreatePromptDraft, useDeletePromptTemplate } from "../prompt";

const CREATE_DRAFT_URL = "/model-hub/prompt-templates/create-draft/";

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
    delete: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/model-hub/prompt-templates/create-draft/",
        promptTemplateId: (id) => `/model-hub/prompt-base-templates/${id}/`,
      },
    },
  },
}));

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }

  QueryWrapper.propTypes = {
    children: PropTypes.node,
  };

  return QueryWrapper;
}

describe("useCreatePromptDraft", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("posts the canonical create-draft body from plain args", async () => {
    axios.post.mockResolvedValueOnce({ data: { result: { id: "draft-1" } } });

    const { result } = renderHook(() => useCreatePromptDraft(), {
      wrapper: createQueryWrapper(),
    });

    const configuration = { model: "gpt-4o", response_format: "text" };
    const messages = [{ role: "user", content: [{ type: "text", text: "hi" }] }];

    result.current.mutate({
      configuration,
      messages,
      variableNames: { topic: ["dogs"] },
    });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(CREATE_DRAFT_URL, {
        name: "",
        prompt_config: [{ configuration, messages }],
        variable_names: { topic: ["dogs"] },
      });
    });
  });

  it("omits variable_names when there are none", async () => {
    axios.post.mockResolvedValueOnce({ data: { result: { id: "draft-2" } } });

    const { result } = renderHook(() => useCreatePromptDraft(), {
      wrapper: createQueryWrapper(),
    });

    result.current.mutate({
      configuration: { model: "gpt-4o" },
      messages: [],
      variableNames: {},
    });

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(CREATE_DRAFT_URL, {
        name: "",
        prompt_config: [{ configuration: { model: "gpt-4o" }, messages: [] }],
      });
    });
  });
});

describe("useDeletePromptTemplate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("issues DELETE /model-hub/prompt-base-templates/{id}/ with the exact id", async () => {
    axios.delete.mockResolvedValueOnce({ data: {} });

    const { result } = renderHook(() => useDeletePromptTemplate(), {
      wrapper: createQueryWrapper(),
    });

    result.current.mutate("template-123");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(axios.delete).toHaveBeenCalledTimes(1);
    expect(axios.delete).toHaveBeenCalledWith(
      "/model-hub/prompt-base-templates/template-123/",
    );
  });

  it("forwards onSuccess/onError options to the mutation", async () => {
    axios.delete.mockResolvedValueOnce({ data: {} });
    const onSuccess = vi.fn();

    const { result } = renderHook(
      () => useDeletePromptTemplate({ onSuccess }),
      { wrapper: createQueryWrapper() },
    );

    result.current.mutate("template-abc");

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });
});
