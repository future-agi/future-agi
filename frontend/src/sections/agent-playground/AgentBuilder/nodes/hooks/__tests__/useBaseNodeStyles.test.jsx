/* eslint-disable react/prop-types */
import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import useBaseNodeStyles from "../useBaseNodeStyles";

// ---------------------------------------------------------------------------
// Theme setup
// ---------------------------------------------------------------------------
function makeTheme(mode = "light") {
  return createTheme({
    palette: {
      mode,
      red: { 50: "#fff5f5", 500: "#f44336", 700: "#d32f2f" },
      green: { 50: "#e8f5e9", 500: "#4caf50" },
      blue: { 500: "#2196f3" },
      black: { 200: "#e0e0e0", 400: "#9e9e9e" },
      background: { paper: "#fff" },
    },
  });
}

function renderWithTheme(props, mode = "light") {
  const theme = makeTheme(mode);
  const wrapper = ({ children }) => (
    <ThemeProvider theme={theme}>{children}</ThemeProvider>
  );
  return renderHook(() => useBaseNodeStyles(props), { wrapper });
}

const defaultProps = {
  selected: false,
  hasValidationError: false,
  isRunning: false,
  isCompleted: false,
  isError: false,
  isIdleState: true,
  preview: false,
  maxPortCount: 1,
  nodeHeight: 40,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useBaseNodeStyles", () => {
  // ---- borderColor priority ----
  it("returns red borderColor when hasValidationError", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      hasValidationError: true,
    });
    expect(result.current.borderColor).toContain("f44336");
  });

  it("returns red borderColor when isError", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isError: true,
      isIdleState: false,
    });
    expect(result.current.borderColor).toContain("f44336");
  });

  it("returns green borderColor when isCompleted", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isCompleted: true,
      isIdleState: false,
    });
    expect(result.current.borderColor).toContain("4caf50");
  });

  it("returns blue borderColor when selected", () => {
    const { result } = renderWithTheme({ ...defaultProps, selected: true });
    expect(result.current.borderColor).toContain("2196f3");
  });

  it("returns gray borderColor by default (light mode)", () => {
    const { result } = renderWithTheme(defaultProps, "light");
    expect(result.current.borderColor).toContain("e0e0e0");
  });

  it("returns darker gray borderColor in dark mode", () => {
    const { result } = renderWithTheme(defaultProps, "dark");
    expect(result.current.borderColor).toContain("9e9e9e");
  });

  it("prioritizes error over completed and selected", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      hasValidationError: true,
      isCompleted: true,
      selected: true,
    });
    // Error takes priority
    expect(result.current.borderColor).toContain("f44336");
  });

  // ---- borderStyle ----
  it("returns dashed borderStyle when running", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isRunning: true,
      isIdleState: false,
    });
    expect(result.current.borderStyle).toBe("dashed");
  });

  it("returns solid borderStyle when not running", () => {
    const { result } = renderWithTheme(defaultProps);
    expect(result.current.borderStyle).toBe("solid");
  });

  // ---- backgroundColor ----
  it("returns red background when error", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isError: true,
      isIdleState: false,
    });
    // Light mode: red[50]
    expect(result.current.backgroundColor).toContain("fff5f5");
  });

  it("returns green background when completed", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isCompleted: true,
      isIdleState: false,
    });
    expect(result.current.backgroundColor).toContain("e8f5e9");
  });

  it("returns paper background by default", () => {
    const { result } = renderWithTheme(defaultProps);
    expect(result.current.backgroundColor).toBe("#fff");
  });

  // ---- boxSx properties ----
  it("includes hover styles when idle and not preview", () => {
    const { result } = renderWithTheme(defaultProps);
    expect(result.current.boxSx["&:hover"]).toBeDefined();
    expect(
      result.current.boxSx[
        "&:hover .node-delete-btn, &:focus-within .node-delete-btn"
      ],
    ).toEqual({
      opacity: 1,
      pointerEvents: "auto",
    });
  });

  it("excludes hover styles in preview mode", () => {
    const { result } = renderWithTheme({ ...defaultProps, preview: true });
    expect(result.current.boxSx["&:hover"]).toBeUndefined();
  });

  it("excludes hover styles when not idle", () => {
    const { result } = renderWithTheme({
      ...defaultProps,
      isIdleState: false,
      isRunning: true,
    });
    expect(result.current.boxSx["&:hover"]).toBeUndefined();
  });

  it("uses center alignment when maxPortCount <= 2", () => {
    const { result } = renderWithTheme({ ...defaultProps, maxPortCount: 2 });
    expect(result.current.boxSx.alignItems).toBe("center");
  });

  it("sets cursor to default in preview mode", () => {
    const { result } = renderWithTheme({ ...defaultProps, preview: true });
    expect(result.current.boxSx.cursor).toBe("default");
  });

  it("sets cursor to pointer when not preview", () => {
    const { result } = renderWithTheme(defaultProps);
    expect(result.current.boxSx.cursor).toBe("pointer");
  });
});
