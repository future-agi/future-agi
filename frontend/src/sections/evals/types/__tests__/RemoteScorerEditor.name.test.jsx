import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RemoteScorerEditor from "../RemoteScorerEditor";

// FA-EVALS-02 (evalcreate:name): on the create page the Remote scorer's
// Create checks the same naming rule as Save Evaluation and says why it is
// off, instead of sending a name the server refuses.

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../RemoteTestPanel", () => ({ default: () => null }));
vi.mock("../SigningSecretDialog", () => ({ default: () => null }));

const FORMAT_ERROR = /characters an eval name can't use/i;
const createButton = () =>
  screen.getByRole("button", { name: "Create remote scorer" });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RemoteScorerEditor Create and the eval name", () => {
  it("keeps Create off for a name the server refuses and says why", () => {
    const onSave = vi.fn();
    render(
      <RemoteScorerEditor name="Hallucination detector" onSave={onSave} />,
    );
    expect(createButton()).toBeDisabled();
    expect(screen.getByText(FORMAT_ERROR)).toBeInTheDocument();
    fireEvent.click(createButton());
    expect(onSave).not.toHaveBeenCalled();
  });

  it("still asks for a name first when there is none", () => {
    render(<RemoteScorerEditor name="" onSave={vi.fn()} />);
    expect(createButton()).toBeDisabled();
    expect(screen.getByText("Name the eval first.")).toBeInTheDocument();
  });

  it("does not hold back Create for a valid name", () => {
    render(
      <RemoteScorerEditor name="hallucination_detector" onSave={vi.fn()} />,
    );
    expect(createButton()).toBeEnabled();
    expect(screen.queryByText(FORMAT_ERROR)).toBeNull();
  });
});
