import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import CustomTraceHeaderRenderer from "../Renderers/CustomTraceHeaderRenderer";

const col = (sourceColumn) => ({ colDef: { context: { sourceColumn } } });
const evalCol = (targetType) => ({
  groupBy: "Evaluation Metrics",
  targetType,
});

describe("CustomTraceHeaderRenderer target glyph", () => {
  it("renders S for a spans eval column", () => {
    render(
      <CustomTraceHeaderRenderer displayName="Groundedness" column={col(evalCol("spans"))} />,
    );
    expect(screen.getByText("S")).toBeInTheDocument();
  });

  it("renders T for a traces eval column", () => {
    render(
      <CustomTraceHeaderRenderer displayName="Toxicity" column={col(evalCol("traces"))} />,
    );
    expect(screen.getByText("T")).toBeInTheDocument();
  });

  it("renders no glyph for sessions or voiceCalls", () => {
    for (const t of ["sessions", "voiceCalls"]) {
      const { unmount } = render(
        <CustomTraceHeaderRenderer displayName="Tone" column={col(evalCol(t))} />,
      );
      expect(screen.queryByText("S")).not.toBeInTheDocument();
      expect(screen.queryByText("T")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("renders no glyph when targetType is missing", () => {
    render(
      <CustomTraceHeaderRenderer displayName="Legacy" column={col(evalCol(null))} />,
    );
    expect(screen.queryByText(/^(S|T)$/)).not.toBeInTheDocument();
  });

  it("renders no glyph on a non-eval column even with a targetType", () => {
    render(
      <CustomTraceHeaderRenderer
        displayName="Latency"
        column={col({ groupBy: "Trace Columns", targetType: "spans" })}
      />,
    );
    expect(screen.queryByText(/^(S|T)$/)).not.toBeInTheDocument();
  });

  it("still renders the column name", () => {
    render(
      <CustomTraceHeaderRenderer displayName="Groundedness" column={col(evalCol("spans"))} />,
    );
    expect(screen.getByText("Groundedness")).toBeInTheDocument();
  });
});
