import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const gates = vi.hoisted(() => ({ prototype: false, claimed: false }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => gates.prototype,
}));
vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: (id) => id === "dataset.runPromptEdit" && gates.claimed,
}));

import RerunScopeField from "../RerunScopeField";
import {
  EDIT_RUN_PROMPT_BODY,
  RERUN_EVERY_ROW_LINE,
  RERUN_OPERATION_BODY,
  rerunScopeSupported,
  withRerunScope,
} from "../rerunScope";

beforeEach(() => {
  gates.prototype = false;
  gates.claimed = false;
});

const definitions = [RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY];
const scopes = [
  ["all", "Re-run all rows"],
  ["changed", "Only rows whose inputs changed"],
  ["failed", "Only failed cells"],
];

const bodies = {
  [RERUN_OPERATION_BODY]: {
    operation_type: "extract_json",
    config: { column_id: "input", json_key: "answer" },
  },
  [EDIT_RUN_PROMPT_BODY]: {
    dataset_id: "dataset",
    column_id: "output",
    name: "Answer",
    config: { messages: [{ role: "user", content: "{{input}}" }] },
  },
};

describe("selective rerun contracts", () => {
  it.each(definitions)(
    "%s supports selective saves in real builds without a surface override",
    (definition) => {
      expect(rerunScopeSupported(definition)).toBe(true);
    },
  );

  it.each(definitions)(
    "%s sends each scope without changing the original edit body",
    (definition) => {
      const body = Object.freeze(bodies[definition]);
      for (const [scope] of scopes) {
        const result = withRerunScope(body, scope, definition);
        expect(result).toEqual({ ...body, scope });
        expect(result).not.toBe(body);
        expect(result.config).toBe(body.config);
        expect(body).not.toHaveProperty("scope");
      }
    },
  );

  it.each(definitions)(
    "%s still sends the selected scope when its surface is shown",
    (definition) => {
      gates.claimed = true;
      expect(withRerunScope(bodies[definition], "failed", definition)).toEqual({
        ...bodies[definition],
        scope: "failed",
      });
    },
  );

  it("does not attach a scope to a body whose contract lacks it", () => {
    const body = { dataset_id: "id" };
    expect(rerunScopeSupported("ExtractJsonColumnRequest")).toBe(false);
    expect(withRerunScope(body, "failed", "ExtractJsonColumnRequest")).toBe(
      body,
    );
  });

  it.each([undefined, null, ""])(
    "keeps the original body when scope is %s",
    (scope) => {
      for (const definition of definitions) {
        const body = bodies[definition];
        expect(withRerunScope(body, scope, definition)).toBe(body);
      }
    },
  );

  it("uses the dynamic rerun contract when no definition is passed", () => {
    expect(rerunScopeSupported()).toBe(true);
    expect(withRerunScope(bodies[RERUN_OPERATION_BODY], "changed")).toEqual({
      ...bodies[RERUN_OPERATION_BODY],
      scope: "changed",
    });
  });

  it.each(definitions)(
    "%s offers all row choices in real builds and keeps all rows selected",
    (definition) => {
      render(
        <RerunScopeField
          value="all"
          onChange={() => {}}
          definition={definition}
        />,
      );
      expect(
        screen.getByRole("radiogroup", { name: "Rows to re-run on save" }),
      ).toBeInTheDocument();
      expect(screen.getAllByRole("radio")).toHaveLength(3);
      for (const [scope, label] of scopes) {
        const radio = screen.getByRole("radio", { name: label, exact: true });
        expect(radio).toHaveAttribute("value", scope);
        expect(radio).toBeEnabled();
      }
      expect(
        screen.getByRole("radio", { name: "Re-run all rows" }),
      ).toBeChecked();
      expect(screen.queryByTestId("rerun-scope-line")).toBeNull();
    },
  );

  it.each(definitions)(
    "%s passes real-build row choices to the edit request",
    (definition) => {
      const onChange = vi.fn();
      render(
        <RerunScopeField
          value="all"
          onChange={onChange}
          definition={definition}
        />,
      );
      for (const [scope, label] of scopes.slice(1)) {
        fireEvent.click(
          screen.getByRole("radio", { name: label, exact: true }),
        );
        expect(onChange).toHaveBeenLastCalledWith(scope);
        const selected = onChange.mock.lastCall[0];
        expect(
          withRerunScope(bodies[definition], selected, definition),
        ).toEqual({
          ...bodies[definition],
          scope,
        });
      }
    },
  );

  it("keeps the all-rows explanation for an unsupported request contract", () => {
    render(
      <RerunScopeField
        value="all"
        onChange={() => {}}
        definition="ExtractJsonColumnRequest"
      />,
    );
    expect(screen.getByTestId("rerun-scope-line")).toHaveTextContent(
      RERUN_EVERY_ROW_LINE,
    );
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });

  it.each(definitions)(
    "%s retains selective requests and choices in prototype mode",
    (definition) => {
      gates.prototype = true;
      expect(rerunScopeSupported(definition)).toBe(true);
      for (const [scope] of scopes) {
        expect(withRerunScope(bodies[definition], scope, definition)).toEqual({
          ...bodies[definition],
          scope,
        });
      }
      render(
        <RerunScopeField
          value="failed"
          onChange={() => {}}
          definition={definition}
        />,
      );
      expect(screen.queryAllByRole("radio")).toHaveLength(3);
      expect(
        screen.getByRole("radio", { name: "Only failed cells" }),
      ).toBeChecked();
      expect(screen.queryByTestId("rerun-scope-line")).toBeNull();
    },
  );
});
