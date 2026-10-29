import { describe, it, expect } from "vitest";
import { createTheme } from "@mui/material/styles";
import { alpha } from "@mui/material";
import { START_ID, END_ID } from "../layoutUtils";
import {
  getStatusBorderColor,
  getStatusBackgroundColor,
  isSkippedExecutionStatus,
  isSelectableExecutionNode,
} from "../nodeUtils";

const defaultColor = { light: "#e0e0e0", dark: "#9e9e9e" };

const lightTheme = createTheme({
  palette: {
    mode: "light",
    green: { 50: "#e8f5e9", 500: "#4caf50" },
    red: { 50: "#ffebee", 500: "#f44336", 700: "#d32f2f" },
  },
});

const darkTheme = createTheme({
  palette: {
    mode: "dark",
    green: { 50: "#e8f5e9", 500: "#4caf50" },
    red: { 50: "#ffebee", 500: "#f44336", 700: "#d32f2f" },
  },
});

describe("getStatusBorderColor", () => {
  it("returns green for success and running", () => {
    expect(
      getStatusBorderColor("success", lightTheme, false, defaultColor),
    ).toBe(lightTheme.palette.green[500]);
    expect(
      getStatusBorderColor("running", lightTheme, false, defaultColor),
    ).toBe(lightTheme.palette.green[500]);
  });

  it("returns red for failed and error", () => {
    expect(
      getStatusBorderColor("failed", lightTheme, false, defaultColor),
    ).toBe(lightTheme.palette.red[500]);
    expect(getStatusBorderColor("error", lightTheme, false, defaultColor)).toBe(
      lightTheme.palette.red[500],
    );
  });

  it("returns red for skipped instead of the no-result default", () => {
    expect(
      getStatusBorderColor("skipped", lightTheme, false, defaultColor),
    ).toBe(lightTheme.palette.red[500]);
    expect(
      getStatusBorderColor("SKIPPED", lightTheme, false, defaultColor),
    ).toBe(lightTheme.palette.red[500]);
    expect(getStatusBorderColor("skipped", darkTheme, true, defaultColor)).toBe(
      darkTheme.palette.red[500],
    );
  });

  it("returns the default grey when there is no result", () => {
    expect(
      getStatusBorderColor(undefined, lightTheme, false, defaultColor),
    ).toBe(defaultColor.light);
    expect(getStatusBorderColor(null, darkTheme, true, defaultColor)).toBe(
      defaultColor.dark,
    );
  });
});

describe("getStatusBackgroundColor", () => {
  it("returns a green tint for success", () => {
    expect(getStatusBackgroundColor("success", lightTheme, false)).toBe(
      lightTheme.palette.green[50],
    );
  });

  it("returns a red tint for failed", () => {
    expect(getStatusBackgroundColor("failed", lightTheme, false)).toBe(
      lightTheme.palette.red[50],
    );
  });

  it("returns a skipped red tint instead of the no-result default", () => {
    expect(getStatusBackgroundColor("skipped", lightTheme, false)).toBe(
      alpha(lightTheme.palette.red[500], 0.08),
    );
    expect(getStatusBackgroundColor("skipped", darkTheme, true)).toBe(
      alpha(darkTheme.palette.red[500], 0.16),
    );
    expect(getStatusBackgroundColor(undefined, lightTheme, false)).toBeNull();
  });
});

describe("isSkippedExecutionStatus", () => {
  it("matches skipped case-insensitively", () => {
    expect(isSkippedExecutionStatus("skipped")).toBe(true);
    expect(isSkippedExecutionStatus("SKIPPED")).toBe(true);
    expect(isSkippedExecutionStatus("success")).toBe(false);
    expect(isSkippedExecutionStatus(undefined)).toBe(false);
  });
});

describe("isSelectableExecutionNode", () => {
  it("rejects start and end markers", () => {
    expect(isSelectableExecutionNode({ id: START_ID })).toBe(false);
    expect(isSelectableExecutionNode({ id: END_ID })).toBe(false);
  });

  it("rejects skipped nodes so they cannot open the empty panel", () => {
    expect(
      isSelectableExecutionNode({
        id: "n1",
        data: { nodeExecution: { status: "skipped" } },
      }),
    ).toBe(false);
    expect(
      isSelectableExecutionNode({
        id: "n1",
        node_execution: { status: "SKIPPED" },
      }),
    ).toBe(false);
  });

  it("allows success, failed, and running nodes", () => {
    expect(
      isSelectableExecutionNode({
        id: "n1",
        data: { nodeExecution: { status: "success" } },
      }),
    ).toBe(true);
    expect(
      isSelectableExecutionNode({
        id: "n1",
        data: { nodeExecution: { status: "failed" } },
      }),
    ).toBe(true);
    expect(
      isSelectableExecutionNode({
        id: "n1",
        data: { nodeExecution: { status: "running" } },
      }),
    ).toBe(true);
  });

  it("allows a never-run node at the click-helper layer (canvas still ignores it)", () => {
    expect(isSelectableExecutionNode({ id: "n1", data: {} })).toBe(true);
  });
});
