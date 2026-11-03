import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

const mocks = vi.hoisted(() => ({ put: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    develop: {
      eval: {
        updateEvalTemplate: (id) => `/model-hub/eval-templates/${id}/update/`,
      },
    },
  },
}));

import { toEvalUpdatePayload, useUpdateEval } from "../useEvalDetail";

const TEMPLATE_ID = "0b3c4c2e-5d0e-4c4e-9f43-3a9f3b8f2c11";

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
}

async function sendUpdate(payload) {
  const { result } = renderHook(() => useUpdateEval(TEMPLATE_ID), {
    wrapper: createQueryWrapper(),
  });
  await act(async () => {
    await result.current.mutateAsync(payload);
  });
  const [url, body] = mocks.put.mock.calls[0];
  return { url, body };
}

describe("useUpdateEval model handling", () => {
  beforeEach(() => {
    mocks.put.mockReset();
    mocks.put.mockResolvedValue({ data: { result: {} } });
  });

  it("omits a blank model (no default model on self-hosted) so autosave does not 400", async () => {
    const { url, body } = await sendUpdate({
      eval_type: "llm",
      model: "",
      output_type: "pass_fail",
    });

    expect(body).not.toHaveProperty("model");
    expect(body).toMatchObject({ eval_type: "llm", output_type: "pass_fail" });
    // The backend contract rejects model: "" with "This field may not be blank."
    expect(
      validateContractedRequestConfig({ url, method: "put", data: body }),
    ).toMatchObject({ ok: true });
    expect(
      validateContractedRequestConfig({
        url,
        method: "put",
        data: { eval_type: "llm", model: "" },
      }).ok,
    ).toBe(false);
  });

  it("never sends a model for code evals", async () => {
    const { body } = await sendUpdate({
      eval_type: "code",
      code: "def evaluate(**kwargs):\n    return True\n",
      code_language: "python",
      model: "turing_large",
    });

    expect(body).not.toHaveProperty("model");
    expect(body.code_language).toBe("python");
  });

  it("keeps a chosen model for LLM evals", async () => {
    const { body } = await sendUpdate({ eval_type: "llm", model: "gpt-4o" });

    expect(body.model).toBe("gpt-4o");
  });

  it("keeps a chosen model when the payload has no eval_type", () => {
    expect(toEvalUpdatePayload({ model: "gpt-4o", name: "x" })).toEqual({
      model: "gpt-4o",
      name: "x",
    });
    expect(toEvalUpdatePayload({ model: "", name: "x" })).toEqual({
      name: "x",
    });
  });
});
