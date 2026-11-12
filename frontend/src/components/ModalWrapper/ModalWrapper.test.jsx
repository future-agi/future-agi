import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import ModalWrapper from "./ModalWrapper";

const TITLE = "Rename folder";
const SUBTITLE = "The new name shows everywhere this folder is listed.";

function FolderForm() {
  return (
    <div>
      <label htmlFor="folder-name">Folder name</label>
      <input id="folder-name" defaultValue="Billing prompts" />
    </div>
  );
}

function renderModal(props = {}) {
  const onClose = vi.fn();
  const onSubmit = vi.fn();
  render(
    <ModalWrapper
      open
      title={TITLE}
      onClose={onClose}
      onSubmit={onSubmit}
      isValid
      {...props}
    >
      <FolderForm />
    </ModalWrapper>,
  );
  return { onClose, onSubmit };
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

function idsOf(element, attribute) {
  return (element.getAttribute(attribute) || "").split(/\s+/).filter(Boolean);
}

function browserName(dialog) {
  return idsOf(dialog, "aria-labelledby")
    .map((id) => browserText(document.getElementById(id)))
    .join(" ");
}

function nameOf(element) {
  return (
    element.getAttribute("aria-label") ||
    (element.labels && element.labels[0] && element.labels[0].textContent) ||
    element.textContent
  ).trim();
}

function byDocumentOrder(a, b) {
  return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING
    ? -1
    : 1;
}

describe("ModalWrapper accessible name and description", () => {
  it("is named by its title alone, without the close button", () => {
    renderModal();

    const dialog = screen.getByRole("dialog", { name: TITLE });
    expect(browserName(dialog)).toBe(TITLE);
    expect(dialog).toHaveAccessibleName(TITLE);
  });

  it("is named by its title alone when a subtitle is given", () => {
    renderModal({ subTitle: SUBTITLE });

    const dialog = screen.getByRole("dialog", { name: TITLE });
    expect(browserName(dialog)).toBe(TITLE);
  });

  it("keeps the close button out of the dialog's label and out of every heading", () => {
    renderModal({ subTitle: SUBTITLE });

    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    const labelIds = idsOf(dialog, "aria-labelledby");
    expect(labelIds.length).toBeGreaterThan(0);
    labelIds.forEach((id) => {
      const label = document.getElementById(id);
      expect(label).not.toBeNull();
      expect(label.contains(close)).toBe(false);
      expect(within(label).queryByRole("button")).toBeNull();
    });
    within(dialog)
      .queryAllByRole("heading")
      .forEach((heading) => {
        expect(heading.contains(close)).toBe(false);
      });
  });

  it("has one heading, and it reads the title only", () => {
    renderModal({ subTitle: SUBTITLE });

    const headings = within(screen.getByRole("dialog")).getAllByRole("heading");
    expect(headings).toHaveLength(1);
    expect(browserText(headings[0])).toBe(TITLE);
  });

  it("is described by its subtitle only, never by the form inside it", () => {
    renderModal({ subTitle: SUBTITLE });

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(SUBTITLE);
    const describedBy = idsOf(dialog, "aria-describedby");
    expect(describedBy).toHaveLength(1);
    expect(countIds(describedBy[0])).toBe(1);
    const description = document.getElementById(describedBy[0]);
    expect(dialog.contains(description)).toBe(true);
    expect(browserText(description)).toBe(SUBTITLE);
    expect(within(description).queryByRole("textbox")).toBeNull();
  });

  it("has no description when there is no subtitle, even with a form inside", () => {
    renderModal();

    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByRole("textbox", { name: "Folder name" }),
    ).toBeInTheDocument();
    expect(dialog).not.toHaveAttribute("aria-describedby");
    expect(dialog).toHaveAccessibleDescription("");
  });

  it("is not named by its close button and has no empty heading when there is no title", () => {
    renderModal({ title: undefined });

    const dialog = screen.getByRole("dialog");
    expect(browserName(dialog)).toBe("");
    expect(within(dialog).queryAllByRole("heading")).toHaveLength(0);
    expect(
      within(dialog).getByRole("button", { name: "Close dialog" }),
    ).toBeInTheDocument();
  });

  it("gives two dialogs in one tree their own label ids, each naming its own title", () => {
    render(
      <>
        <ModalWrapper open title="Rename folder" onClose={() => {}}>
          <FolderForm />
        </ModalWrapper>
        <ModalWrapper
          open
          title="Move prompt"
          subTitle="Choose the folder this prompt moves to."
          onClose={() => {}}
        >
          <p>Billing prompts</p>
        </ModalWrapper>
      </>,
    );

    // The second modal hides the first from assistive technology.
    const [first, second] = screen.getAllByRole("dialog", { hidden: true });
    const [firstId] = idsOf(first, "aria-labelledby");
    const [secondId] = idsOf(second, "aria-labelledby");
    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
    expect(countIds(firstId)).toBe(1);
    expect(countIds(secondId)).toBe(1);
    expect(first.contains(document.getElementById(firstId))).toBe(true);
    expect(second.contains(document.getElementById(secondId))).toBe(true);
    expect(browserName(first)).toBe("Rename folder");
    expect(browserName(second)).toBe("Move prompt");
  });
});

describe("ModalWrapper close button and keyboard", () => {
  it("closes from the close button", () => {
    const { onClose } = renderModal({ subTitle: SUBTITLE });

    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on Escape when it is not loading", () => {
    const { onClose } = renderModal();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps the close button disabled and ignores Escape while loading", () => {
    const { onClose } = renderModal({ subTitle: SUBTITLE, isLoading: true });

    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    expect(close).toBeDisabled();
    fireEvent.click(close);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps the keyboard order: close button, the form, then Cancel and the action", () => {
    renderModal({ subTitle: SUBTITLE, actionBtnTitle: "Rename" });

    // No control sets a positive tabindex, so keyboard order is document
    // order.
    const dialog = screen.getByRole("dialog");
    const controls = [
      ...within(dialog).getAllByRole("button"),
      ...within(dialog).getAllByRole("textbox"),
    ];
    controls.sort(byDocumentOrder);
    expect(controls.map(nameOf)).toEqual([
      "Close dialog",
      "Folder name",
      "Cancel",
      "Rename",
    ]);
  });

  it("keeps the close button 12px from the top and right of the dialog paper", () => {
    renderModal({ subTitle: SUBTITLE });

    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close dialog" });
    const style = window.getComputedStyle(close);
    expect(style.position).toBe("absolute");
    expect(style.top).toBe("12px");
    expect(style.right).toBe("12px");
    // The paper is the box the button is placed against: it is positioned,
    // and nothing between the button and the paper is.
    expect(window.getComputedStyle(dialog).position).toBe("relative");
    for (let el = close.parentElement; el !== dialog; el = el.parentElement) {
      expect(["", "static"]).toContain(window.getComputedStyle(el).position);
    }
  });
});
