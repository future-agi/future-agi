import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RetreiveSpan from "./RetrieveSpan";

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <span>{text}</span>,
}));

vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));

vi.mock("src/components/custom-json-viewer/CustomJsonViewer", () => ({
  default: ({ object }) => <span>{JSON.stringify(object)}</span>,
}));

describe("RetreiveSpan", () => {
  it("renders the fallback value when no documents are retrieved", () => {
    render(
      <RetreiveSpan
        value={{ cellValue: "fallback value" }}
        column={{ headerName: "Retrieved docs" }}
        retreiveDocs={{}}
      />,
    );

    expect(screen.getByText("Retrieved docs")).toBeInTheDocument();
    expect(screen.getByText(/fallback value/)).toBeInTheDocument();
    expect(screen.queryByText(/Document /)).not.toBeInTheDocument();
  });

  it("renders a single retrieved document instead of the fallback value", () => {
    render(
      <RetreiveSpan
        value={{ cellValue: "fallback value" }}
        column={{ headerName: "Retrieved docs" }}
        showScore
        retreiveDocs={{
          doc1: {
            id: "doc1",
            value: "retrieved content",
            score: 0.9,
          },
        }}
      />,
    );

    expect(screen.getByText("Retrieved docs (1)")).toBeInTheDocument();
    expect(screen.getByText("Document doc1")).toBeInTheDocument();
    expect(screen.getByText("Score - 0.9")).toBeInTheDocument();
    expect(screen.getByText(/retrieved content/)).toBeInTheDocument();
    expect(screen.queryByText(/fallback value/)).not.toBeInTheDocument();
  });

  it("renders every retrieved document instead of the fallback value", () => {
    render(
      <RetreiveSpan
        value={{ cellValue: "fallback value" }}
        column={{ headerName: "Retrieved docs" }}
        showScore
        retreiveDocs={{
          doc1: {
            id: "doc1",
            value: "first retrieved content",
            score: 0.9,
          },
          doc2: {
            id: "doc2",
            value: "second retrieved content",
            score: 0.8,
          },
        }}
      />,
    );

    expect(screen.getByText("Retrieved docs (2)")).toBeInTheDocument();
    expect(screen.getByText("Document doc1")).toBeInTheDocument();
    expect(screen.getByText("Document doc2")).toBeInTheDocument();
    expect(screen.getByText("Score - 0.9")).toBeInTheDocument();
    expect(screen.getByText("Score - 0.8")).toBeInTheDocument();
    expect(screen.getByText(/first retrieved content/)).toBeInTheDocument();
    expect(screen.getByText(/second retrieved content/)).toBeInTheDocument();
    expect(screen.queryByText(/fallback value/)).not.toBeInTheDocument();
  });
});
