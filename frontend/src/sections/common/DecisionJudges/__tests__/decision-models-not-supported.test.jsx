// A gateway older than the decision-model listing answers the backend's typed
// refusal (501 decisions_not_supported). The picker says so in one plain line
// with no "Try again", since retrying cannot help; any other failed listing
// keeps "Couldn't list decision models · Try again".
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderHook, screen } from "@testing-library/react";
import { render } from "src/utils/test-utils";

const state = vi.hoisted(() => ({ query: null, options: null }));

vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => ({ data: [] }),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: (options) => {
    state.options = options;
    return state.query;
  },
}));

import DecisionModelsSection from "../DecisionModelsSection";
import { useDecisionModels } from "../useDecisionModels";

const NOT_SUPPORTED =
  "This gateway version doesn't list decision models. Update the gateway to use them.";
const notSupportedError = {
  response: { status: 501, data: { code: "decisions_not_supported" } },
};
const badGatewayError = {
  response: { status: 502, data: { code: "gateway_unavailable" } },
};

describe("the decision-model listing behind an older gateway", () => {
  beforeEach(() => {
    state.query = null;
    state.options = null;
  });

  it("the hook reports the typed refusal, and does not retry it", () => {
    state.query = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: notSupportedError,
      refetch: vi.fn(),
    };
    const { result } = renderHook(() => useDecisionModels());
    expect(result.current.isError).toBe(true);
    expect(result.current.notSupported).toBe(true);
    expect(state.options.retry(0, notSupportedError)).toBe(false);
    expect(state.options.retry(0, badGatewayError)).toBe(true);
    expect(state.options.retry(1, badGatewayError)).toBe(false);
  });

  it("the hook keeps any other failure retryable", () => {
    state.query = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: badGatewayError,
      refetch: vi.fn(),
    };
    const { result } = renderHook(() => useDecisionModels());
    expect(result.current.isError).toBe(true);
    expect(result.current.notSupported).toBe(false);
  });
});

describe("DecisionModelsSection behind an older gateway", () => {
  it("shows one plain line, with no Try again", () => {
    const refetch = vi.fn();
    state.query = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: notSupportedError,
      refetch,
    };
    render(<DecisionModelsSection onPick={() => {}} onAddKey={() => {}} />);
    const line = screen.getByTestId("decision-models-not-supported");
    expect(line.textContent).toBe(NOT_SUPPORTED);
    expect(screen.queryByText(/Try again/)).toBeNull();
    expect(screen.queryByTestId("decision-models-error")).toBeNull();
    fireEvent.click(line);
    expect(refetch).not.toHaveBeenCalled();
  });

  it("keeps Couldn't list decision models · Try again for a 502", () => {
    const refetch = vi.fn();
    state.query = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: badGatewayError,
      refetch,
    };
    render(<DecisionModelsSection onPick={() => {}} onAddKey={() => {}} />);
    const line = screen.getByTestId("decision-models-error");
    expect(line.textContent).toBe("Couldn't list decision models · Try again");
    expect(screen.queryByTestId("decision-models-not-supported")).toBeNull();
    fireEvent.click(line);
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
