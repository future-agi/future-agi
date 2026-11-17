import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SessionTurnFieldsDialog from "./SessionTurnFieldsDialog";

afterEach(cleanup);
const fields = [
  { kind: "value", label: "Answer", path: "output.answer" },
  { kind: "metric", metric: "total_cost" },
];

describe("Turn fields dialog", () => {
  it("previews paths, validates roots and saves the ordered descriptors", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <SessionTurnFieldsDialog
        open
        fields={fields}
        sample={{ output: { answer: false } }}
        onSave={onSave}
        onClose={onClose}
      />,
    );
    expect(screen.getByText("→ false")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Path" }), {
      target: { value: "metadata.answer" },
    });
    expect(screen.getByRole("button", { name: "Apply & save" }).disabled).toBe(
      true,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Path" }), {
      target: { value: "output.answer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Move field 2 up" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply & save" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith([fields[1], fields[0]]),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("retains edits after failure and offers an actionable retry", async () => {
    const onSave = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <SessionTurnFieldsDialog
        open
        fields={fields}
        onSave={onSave}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply & save" }));
    await screen.findByRole("alert");
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(onSave).toHaveBeenLastCalledWith([fields[1]]));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("adds and removes fields, allows an empty selection, and cancels without saving", () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <SessionTurnFieldsDialog
        open
        fields={[]}
        onSave={onSave}
        onClose={onClose}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add field" }));
    expect(
      within(screen.getByRole("group", { name: "Field 1" })).getByRole(
        "textbox",
        { name: "Path" },
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
    expect(screen.getByRole("button", { name: "Apply & save" }).disabled).toBe(
      false,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });
});
