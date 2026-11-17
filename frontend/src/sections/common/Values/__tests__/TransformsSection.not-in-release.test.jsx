// The Schema drawer's Transformations part on this server: the schema save
// keeps only the declared schema, so a transformation is never stored. The
// part stays on screen, turned off, with the shared line beside its title;
// every control is disabled and described by that line, and nothing saves.
// The prototype keeps the part as it is.
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setRealBackendForTests } from "src/sections/develop/realBackendCaps";
import TransformsSection from "../TransformsSection";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const LINE = "Not available in this release";
const COLUMNS = [{ id: "col-messages", name: "messages" }];
const SCHEMA = {
  enforce: "off",
  transforms: [{ column: "messages", kind: "trim_whitespace" }],
};

const mount = () => {
  const onSave = vi.fn();
  const onApply = vi.fn();
  render(
    <TransformsSection
      columns={COLUMNS}
      rows={[]}
      valueOf={() => null}
      schema={SCHEMA}
      saving={false}
      onSave={onSave}
      onApply={onApply}
    />,
  );
  return { onSave, onApply };
};

afterEach(() => {
  cleanup();
  setRealBackendForTests(null);
});

describe("Transformations on this server", () => {
  it("stays on screen with the line beside its title, every control turned off and described by it", () => {
    setRealBackendForTests(true);
    const { onSave, onApply } = mount();

    expect(screen.getByText("Transformations")).toBeVisible();
    expect(screen.getByText(LINE)).toBeVisible();
    // The set transformation still reads as set.
    expect(screen.getByTestId("schema-transform-row")).toHaveTextContent(
      "messages",
    );

    const remove = screen.getByRole("button", {
      name: "Remove Trim whitespace on messages",
    });
    const add = screen.getByRole("button", { name: "Add" });
    const apply = screen.getByTestId("schema-transform-apply");
    for (const button of [remove, add, apply]) {
      expect(button).toBeVisible();
      expect(button).toBeDisabled();
      expect(button).toHaveAccessibleDescription(LINE);
    }
    for (const name of ["Transformation column", "Transformation"]) {
      const select = screen.getByRole("combobox", { name });
      expect(select).toHaveAttribute("aria-disabled", "true");
      expect(select).toHaveAccessibleDescription(LINE);
    }

    fireEvent.click(remove);
    fireEvent.click(add);
    fireEvent.click(apply);
    expect(onSave).not.toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
  });

  // Guard (green before and after): the prototype keeps the part working.
  it("keeps Remove working in the prototype", () => {
    setRealBackendForTests(false);
    const { onSave } = mount();

    expect(screen.queryByText(LINE)).toBeNull();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove Trim whitespace on messages",
      }),
    );
    expect(onSave).toHaveBeenCalledWith([]);
  });
});
