import { expect, it, vi } from "vitest";
import { toAgMenuItems, completeAgMenuCheckedState } from "../headerMenuItems";

it("does not label ordinary commands as checkboxes", () => {
  const [command, unchecked, checked] = toAgMenuItems([
    { label: "Hide column", run: vi.fn() },
    { label: "Sort ascending", checked: false },
    { label: "Sort descending", checked: true },
  ]);
  expect(command).not.toHaveProperty("checked");
  expect(unchecked.checked).toBe(false);
  expect(checked.checked).toBe(true);
});

it("supplies AG Grid 35's missing false state without overwriting the checked state", () => {
  const popup = document.createElement("div");
  popup.innerHTML =
    '<div role="menuitem">Hide</div><div role="menuitemcheckbox">Ascending</div><div role="menuitemcheckbox" aria-checked="true">Descending</div>';
  completeAgMenuCheckedState(popup);
  const items = popup.querySelectorAll('[role="menuitemcheckbox"]');
  expect(items[0]).toHaveAttribute("aria-checked", "false");
  expect(items[1]).toHaveAttribute("aria-checked", "true");
  expect(popup.firstChild).not.toHaveAttribute("aria-checked");
});
