// @vitest-environment jsdom
// W9 A0 (W9-SCOPE §5): the Save view dialog offers "Share with everyone in
// this workspace" only when the host passes `canShare`; a viewer sees it
// disabled with the reason; a shared view is captioned `Shared · <author>`
// and Delete is offered only on views the caller may delete.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  SaveViewDialog,
  SavedViewLabel,
  VIEWER_SHARE_REASON,
  canDeleteView,
  savedViewCaption,
} from "../SavedViewsMenu";

const SHARE = "Share with everyone in this workspace";

function open(props) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SaveViewDialog open onClose={() => {}} onSave={onSave} {...props} />);
  return onSave;
}

describe("SaveViewDialog sharing", () => {
  it("shows no Share checkbox when the host passes no canShare", () => {
    open({});
    expect(screen.queryByLabelText(SHARE)).toBeNull();
  });

  it("sends shared: true when a member ticks Share", async () => {
    const onSave = open({ canShare: true });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Golden below 80" },
    });
    fireEvent.click(screen.getByLabelText(SHARE));
    expect(
      screen.getByText(/Everyone in this workspace sees it\./),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith("Golden below 80", { shared: true }),
    );
  });

  it("sends shared: false when Share is left unticked", async () => {
    const onSave = open({ canShare: true });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Mine" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith("Mine", { shared: false }),
    );
  });

  it("a viewer sees Share disabled with the reason", async () => {
    open({ canShare: false });
    const box = screen.getByLabelText(SHARE);
    expect(box.disabled).toBe(true);
    fireEvent.mouseOver(box.closest("span"));
    expect(await screen.findByText(VIEWER_SHARE_REASON)).toBeTruthy();
  });
});

describe("saved view captions and delete", () => {
  it("captions a shared view with its author", () => {
    expect(savedViewCaption({ shared: true, created_by: "Asha Rao" })).toBe(
      "Shared · Asha Rao",
    );
    expect(savedViewCaption({ shared: false, created_by: "Maya Chen" })).toBe(
      "by Maya Chen",
    );
    render(
      <SavedViewLabel
        view={{ name: "Refunds", shared: true, created_by: "Asha Rao" }}
      />,
    );
    expect(screen.getByText("Refunds")).toBeTruthy();
    expect(screen.getByText("Shared · Asha Rao")).toBeTruthy();
  });

  it("offers Delete on own views and to admins only", () => {
    expect(canDeleteView({ mine: true, can_delete: true })).toBe(true);
    expect(canDeleteView({ mine: false, can_delete: false })).toBe(false);
    expect(canDeleteView({ mine: false, can_delete: true })).toBe(true);
    // A view from before W9 (no flags) stays deletable, as it was.
    expect(canDeleteView({})).toBe(true);
  });
});
