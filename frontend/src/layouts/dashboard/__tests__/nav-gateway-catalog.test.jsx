import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useNavGatewayData } from "../config-navigation";

// The rail reads the managed-models status: the catalog lists managed
// models, so a deployment that offers none has no Catalog item (its catalog
// URLs open Auto routing).
const state = vi.hoisted(() => ({
  status: undefined,
  isError: false,
  enabled: [],
}));

vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: ({ enabled = true } = {}) => {
    state.enabled.push(enabled);
    return {
      data: enabled ? state.status : undefined,
      isError: enabled && state.isError,
      failureCount: enabled && state.isError ? 1 : 0,
    };
  },
}));

function wrapperAt(pathname) {
  function Wrapper({ children }) {
    return <MemoryRouter initialEntries={[pathname]}>{children}</MemoryRouter>;
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

const modelsItems = (groups) =>
  groups.find((group) => group.id === "models").items.map((i) => i.title);

describe("the AI Gateway rail's Catalog item", () => {
  beforeEach(() => {
    state.status = undefined;
    state.isError = false;
    state.enabled.length = 0;
  });

  it("is left out on a deployment that offers no managed models", () => {
    state.status = { available: false };
    const { result } = renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    expect(modelsItems(result.current)).toEqual(["Auto routing"]);
  });

  it("is listed when the deployment offers managed models", () => {
    state.status = { available: true, enabled: false };
    const { result } = renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    expect(modelsItems(result.current)).toEqual(["Catalog", "Auto routing"]);
  });

  // The rail waits for the status, so a deployment with no managed models
  // never shows a Catalog item, not even for a moment.
  it("renders no rail until the status is known", () => {
    const { result } = renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    expect(result.current).toEqual([]);
  });

  it("is left out when the status cannot be read", () => {
    state.isError = true;
    const { result } = renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    expect(modelsItems(result.current)).toEqual(["Auto routing"]);
  });

  it("reads the status only on AI Gateway pages", () => {
    renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/observe"),
    });
    expect(state.enabled).not.toContain(true);
  });
});
