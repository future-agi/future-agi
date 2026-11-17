/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import DagEvalEditor from "../DagEvalEditor";

// FA-EVALS-02 (evalcreate:name): the Name field keeps what was typed, so the
// Decision tree Save checks the same naming rule as Save Evaluation before it
// sends anything, and a refused save says the server's reason.

const state = vi.hoisted(() => ({ snackbar: vi.fn() }));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: state.snackbar,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The definition editor is replaced by one control that writes a complete
// tree, so the only thing that can keep Save off is the name.
vi.mock("../DagDefinitionEditor", () => ({
  default: ({ onChange }) => (
    <button
      type="button"
      onClick={() =>
        onChange({
          schema: "fi-dag-v1",
          root: "n1",
          output_type: "score",
          nodes: [
            {
              id: "n1",
              kind: "judge",
              question: "Is {{output}} polite?",
              answers: [
                { label: "Yes", next: "l1" },
                { label: "No", next: "l2" },
              ],
            },
            { id: "l1", kind: "leaf", label: "Polite", score: 1 },
            { id: "l2", kind: "leaf", label: "Rude", score: 0 },
          ],
        })
      }
    >
      Use a complete tree
    </button>
  ),
}));

const FORMAT_ERROR = /characters an eval name can't use/i;

const mountEditor = (props) => {
  render(<DagEvalEditor onSave={vi.fn()} {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Use a complete tree" }));
  return screen.getByTestId("dag-save");
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DagEvalEditor Save and the eval name", () => {
  it("keeps Save off for a name the server refuses and says why", async () => {
    const onSave = vi.fn();
    const save = mountEditor({ name: "Hallucination detector", onSave });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.mouseOver(save.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(FORMAT_ERROR);
  });

  it("saves a complete tree under a valid name", async () => {
    const onSave = vi.fn(async () => ({ id: "dag-1" }));
    const save = mountEditor({ name: "hallucination_detector", onSave });
    expect(save).toBeEnabled();
    await act(async () => {
      fireEvent.click(save);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][1]).toEqual(
      expect.objectContaining({ output_type: "score" }),
    );
  });

  it("says the server's reason when it refuses the tree", async () => {
    // utils/axios rejects with the response body spread onto the error.
    const refusal = {
      errors: ["An evaluation with this name already exists."],
      error: "An evaluation with this name already exists.",
      statusCode: 400,
    };
    const onSave = vi.fn(async () => {
      throw refusal;
    });
    const save = mountEditor({ name: "hallucination_detector", onSave });
    await act(async () => {
      fireEvent.click(save);
    });
    expect(state.snackbar).toHaveBeenCalledWith(
      "An evaluation with this name already exists.",
      expect.objectContaining({ variant: "error" }),
    );
  });
});
