import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useCreatePromptDraft } from "../prompt";

const CREATE_DRAFT_URL = "/model-hub/prompt-templates/create-draft/";

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
  endpoints: {
    develop: {
      runPrompt: {
        createPromptDraft: "/model-hub/prompt-templates/create-draft/",
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
