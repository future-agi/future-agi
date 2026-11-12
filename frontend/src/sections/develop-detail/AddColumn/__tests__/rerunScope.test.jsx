import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

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

describe("selective rerun contract gates", () => {
  it.each([RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY])(
    "%s stays hidden without its claim",
    (definition) => {
      expect(rerunScopeSupported(definition, false)).toBe(false);
      const body = { config: {} };
      expect(withRerunScope(body, "failed", definition, false)).toBe(body);
    },
  );

  it.each([RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY])(
    "%s keeps real edit bodies scope-free even after its claim",
    (definition) => {
      gates.claimed = true;
      expect(rerunScopeSupported(definition, false)).toBe(false);
      const body = { config: {} };
      for (const scope of ["all", "changed", "failed"]) {
        const result = withRerunScope(body, scope, definition, false);
        expect(result).toBe(body);
        expect(result).not.toHaveProperty("scope");
      }
    },
  );

  it("does not attach a scope to a body whose contract lacks it", () => {
    gates.claimed = true;
    const body = { dataset_id: "id" };
    expect(
      withRerunScope(body, "failed", "ExtractJsonColumnRequest", false),
    ).toBe(body);
  });

  it("keeps the fallback explanation while hidden", () => {
    render(<RerunScopeField value="all" onChange={() => {}} />);
    expect(screen.getByTestId("rerun-scope-line")).toHaveTextContent(
      RERUN_EVERY_ROW_LINE,
    );
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });

  it.each([RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY])(
    "explains the real all-rows save for %s even after its claim",
    (definition) => {
      gates.claimed = true;
      render(
        <RerunScopeField
          value="failed"
          onChange={() => {}}
          definition={definition}
        />,
      );
      expect(screen.queryAllByRole("radio")).toHaveLength(0);
      expect(screen.getByTestId("rerun-scope-line")).toHaveTextContent(
        RERUN_EVERY_ROW_LINE,
      );
    },
  );

  it.each([RERUN_OPERATION_BODY, EDIT_RUN_PROMPT_BODY])(
    "%s retains selective requests and choices in prototype mode",
    (definition) => {
      gates.prototype = true;
      expect(rerunScopeSupported(definition)).toBe(true);
      for (const scope of ["all", "changed", "failed"]) {
        expect(withRerunScope({ config: {} }, scope, definition)).toEqual({
          config: {},
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
