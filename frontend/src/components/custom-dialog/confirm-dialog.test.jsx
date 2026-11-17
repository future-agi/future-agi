import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import ConfirmDialog from "./confirm-dialog";

const CONTENT = "The card ending in 4242 is removed from this workspace.";

function renderDialog(props = {}) {
  const onClose = vi.fn();
  const onRemove = vi.fn();
  render(
    <ConfirmDialog
      open
      title="Remove card"
      content={CONTENT}
      onClose={onClose}
      action={
        <button type="button" onClick={onRemove}>
          Remove
        </button>
      }
      {...props}
    />,
  );
  return { onClose, onRemove };
}

function countIds(id) {
  return document.querySelectorAll(`[id="${id}"]`).length;
}

// A browser builds a name from content out of the text inside the element
// and the aria-label of every element inside it, buttons included.
// testing-library's name check leaves out a button inside the label, so
// these tests also read the name the way a browser does.
function textAlternative(node) {
  if (!node) return "";
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  if (node.getAttribute("aria-hidden") === "true") return "";
  const label = (node.getAttribute("aria-label") || "").trim();
  if (label) return ` ${label} `;
  return ` ${Array.from(node.childNodes).map(textAlternative).join("")} `;
}

function browserText(node) {
  return textAlternative(node).replace(/\s+/g, " ").trim();
}

function browserName(dialog) {
  return (dialog.getAttribute("aria-labelledby") || "")
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => browserText(document.getElementById(id)))
    .join(" ");
}

describe("ConfirmDialog accessible name and description", () => {
  it("is named by its title only, and its close button keeps its own name", () => {
    const { onClose } = renderDialog();

    const dialog = screen.getByRole("dialog");
    expect(browserName(dialog)).toBe("Remove card");
    expect(dialog).toHaveAccessibleName("Remove card");
    const label = document.getElementById(
      dialog.getAttribute("aria-labelledby"),
    );
    expect(within(label).queryByRole("button")).toBeNull();

    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("has a heading that reads the title only", () => {
    renderDialog();

    const heading = within(screen.getByRole("dialog")).getByRole("heading", {
      level: 2,
    });
    expect(browserText(heading)).toBe("Remove card");
  });

  it("keeps the close button first in keyboard order, before Cancel and the action", () => {
    renderDialog();

    const names = within(screen.getByRole("dialog"))
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") || button.textContent);
    expect(names).toEqual(["Close dialog", "Cancel", "Remove"]);
  });

  it("is described by its content when content is given", () => {
    renderDialog();

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(CONTENT);
    const describedBy = dialog.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(countIds(describedBy)).toBe(1);
    expect(dialog.contains(document.getElementById(describedBy))).toBe(true);
  });

  it("has no aria-describedby when there is no content", () => {
    renderDialog({ content: null });

    expect(screen.getByRole("dialog")).not.toHaveAttribute("aria-describedby");
  });

  it("keeps an aria-labelledby the caller passes, naming the dialog by its title", () => {
    renderDialog({ "aria-labelledby": "remove-card-dialog-title" });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute(
      "aria-labelledby",
      "remove-card-dialog-title",
    );
    expect(countIds("remove-card-dialog-title")).toBe(1);
    expect(browserName(dialog)).toBe("Remove card");
  });

  it("keeps an aria-describedby the caller passes", () => {
    render(
      <ConfirmDialog
        open
        title="Remove card"
        content={CONTENT}
        onClose={() => {}}
        aria-describedby="remove-card-note"
      />,
    );

    expect(screen.getByRole("dialog")).toHaveAttribute(
      "aria-describedby",
      "remove-card-note",
    );
  });

  it("passes a caller's id and aria-label through to the same element as before", () => {
    renderDialog({ id: "remove-card-modal", "aria-label": "Card removal" });

    const root = document.getElementById("remove-card-modal");
    expect(root).not.toBeNull();
    expect(countIds("remove-card-modal")).toBe(1);
    expect(root).toHaveClass("MuiDialog-root");
    expect(root).toHaveAttribute("aria-label", "Card removal");
  });

  it("disables both the close button and Cancel when closeDisabled is set", () => {
    renderDialog({ closeDisabled: true });

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Close dialog" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Remove" }),
    ).toBeEnabled();
  });

  it("gives two dialogs in one tree different title ids", () => {
    render(
      <>
        <ConfirmDialog
          open
          title="Remove card"
          content={CONTENT}
          onClose={() => {}}
        />
        <ConfirmDialog
          open
          title="Delete key"
          content="The key stops working at once."
          onClose={() => {}}
        />
      </>,
    );

    // The second modal hides the first from assistive technology.
    const [first, second] = screen.getAllByRole("dialog", { hidden: true });
    const firstId = first.getAttribute("aria-labelledby");
    const secondId = second.getAttribute("aria-labelledby");
    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
    expect(countIds(firstId)).toBe(1);
    expect(countIds(secondId)).toBe(1);
    expect(first.contains(document.getElementById(firstId))).toBe(true);
    expect(second.contains(document.getElementById(secondId))).toBe(true);
    expect(document.getElementById(firstId)).toHaveTextContent("Remove card");
    expect(document.getElementById(secondId)).toHaveTextContent("Delete key");
  });
});

// A description is read out as one flat string after the dialog's name, so
// content that holds a form or another control must not become it.
describe("ConfirmDialog description when its content holds a control", () => {
  it("is described by rich text content that holds no control", () => {
    renderDialog({
      content: (
        <span>
          The card ending in <strong>4242</strong> is removed.
        </span>
      ),
    });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(
      "The card ending in 4242 is removed.",
    );
  });

  it("is not described by content that holds a text field", () => {
    renderDialog({
      content: (
        <div>
          <p>Type the workspace name to confirm.</p>
          <label htmlFor="confirm-workspace-name">Workspace name</label>
          <input id="confirm-workspace-name" />
        </div>
      ),
    });

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("textbox", { name: "Workspace name" }),
    ).toBeInTheDocument();
    expect(dialog).not.toHaveAttribute("aria-describedby");
    expect(dialog).toHaveAccessibleDescription("");
    expect(dialog).toHaveAccessibleName("Remove card");
  });

  it("is not described by content that holds a link", () => {
    renderDialog({
      content: (
        <p>
          Read <a href="/docs/billing">how billing works</a> first.
        </p>
      ),
    });

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("link", { name: "how billing works" }),
    ).toBeInTheDocument();
    expect(dialog).not.toHaveAttribute("aria-describedby");
  });

  it("stops being described when a control appears in its content, and is described again when it goes", async () => {
    let setShowField;
    function LateField() {
      const [show, setShow] = useState(false);
      setShowField = setShow;
      return (
        <div>
          <p>Name the copy.</p>
          {show && <input aria-label="Copy name" />}
        </div>
      );
    }
    renderDialog({ content: <LateField /> });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription("Name the copy.");

    act(() => setShowField(true));
    await waitFor(() => {
      expect(dialog).not.toHaveAttribute("aria-describedby");
    });

    act(() => setShowField(false));
    await waitFor(() => {
      expect(dialog).toHaveAccessibleDescription("Name the copy.");
    });
  });
});
