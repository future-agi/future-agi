import { describe, it, expect } from "vitest";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import useEnvironmentsTab from "../hooks/useEnvironmentsTab";

const makeWrapper = (initialEntries) => {
  const Wrapper = ({ children }) => (
    <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

const renderTab = (entry) =>
  renderHook(() => ({ ...useEnvironmentsTab(), location: useLocation() }), {
    wrapper: makeWrapper([entry]),
  });

describe("useEnvironmentsTab", () => {
  it("defaults to build when no tab param is present", () => {
    const { result } = renderTab("/x");
    expect(result.current.tab).toBe("build");
  });

  it("reads a valid tab param", () => {
    const { result } = renderTab("/x?tab=my");
    expect(result.current.tab).toBe("my");
  });

  it("falls back to build for an unknown tab param", () => {
    const { result } = renderTab("/x?tab=bogus");
    expect(result.current.tab).toBe("build");
  });

  it("setTab writes the tab to the url", () => {
    const { result } = renderTab("/x");
    act(() => result.current.setTab("my"));
    expect(result.current.tab).toBe("my");
    expect(result.current.location.search).toContain("tab=my");
  });
});
