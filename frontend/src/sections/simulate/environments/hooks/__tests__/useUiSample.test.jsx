import { describe, it, expect } from "vitest";
import PropTypes from "prop-types";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import useUiSample from "../useUiSample";

const at = (entry, feature) => {
  const Wrapper = ({ children }) => (
    <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return renderHook(() => useUiSample(feature), { wrapper: Wrapper }).result
    .current;
};

describe("useUiSample", () => {
  it("is false without the param", () => {
    expect(at("/x", "gaps")).toBe(false);
  });

  it("reads one feature", () => {
    expect(at("/x?uiSample=gaps", "gaps")).toBe(true);
    expect(at("/x?uiSample=gaps", "versions")).toBe(false);
  });

  it("reads a comma list", () => {
    expect(at("/x?uiSample=versions,gaps", "gaps")).toBe(true);
    expect(at("/x?uiSample=versions, gaps", "gaps")).toBe(true);
  });

  it("turns on every feature with all", () => {
    expect(at("/x?uiSample=all", "anything")).toBe(true);
  });
});
