// RL-917: show Mode and Fail Open as read-only fields using the gateway's
// effective values, never the organization's stored pipeline settings.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

const role = vi.hoisted(() => ({ orgLevel: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => role,
}));
const STORED = { fail_open: true, pipeline_mode: "parallel", checks: {} };

describe("917: effective, read-only Fail Open and Mode", () => {
  it.each([true, false])(
    "keeps both fields visible when enabled=%s",
    async (enabled) => {
      const onChange = vi.fn();
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={onChange}
          nodePipeline={{ enabled, fail_open: false, mode: "sequential" }}
        />,
      );
      const mode = screen.getByRole("textbox", { name: "Mode" });
      const failOpen = screen.getByRole("textbox", { name: "Fail Open" });
      expect(mode).toHaveValue("Sequential");
      expect(failOpen).toHaveValue("Off");
      for (const field of [mode, failOpen]) {
        expect(field).toHaveAttribute("readonly");
        await userEvent.type(field, "changed");
      }
      expect(mode).toHaveValue("Sequential");
      expect(failOpen).toHaveValue("Off");
      expect(
        screen.getAllByText(
          "Mode and Fail Open are set by this gateway and are read-only here.",
        ),
      ).toHaveLength(1);
      expect(onChange).not.toHaveBeenCalled();
    },
  );

  it("shows the reported fail-open value, not a stored false value", () => {
    render(
      <GuardrailConfigTab
        guardrails={{ ...STORED, fail_open: false }}
        onChange={vi.fn()}
        nodePipeline={{ enabled: true, fail_open: true, mode: "sequential" }}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Fail Open" })).toHaveValue(
      "On",
    );
  });

  describe.each([true, false])("with guardrails enabled=%s", (enabled) => {
    it.each([
      [
        true,
        "With guardrails on, this gateway's default is to skip a check that times out or panics and continue. Failed synchronous request rewrites are still refused, and a remote-service error still follows the check's threshold and action.",
      ],
      [
        false,
        "With guardrails on, this gateway's default is to treat check errors or timeouts as failures. A check's action can block, warn or log. Failed synchronous request rewrites are still refused.",
      ],
    ])("qualifies effective Fail Open=%s once", (failOpen, explanation) => {
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={vi.fn()}
          nodePipeline={{ enabled, fail_open: failOpen, mode: "sequential" }}
        />,
      );
      expect(screen.getAllByText(explanation)).toHaveLength(1);
      expect(
        screen.queryByText(/A check that errors or times out (lets|blocks)/),
      ).toBeNull();
    });
  });

  it.each([
    undefined,
    null,
    {},
    { enabled: true },
    { mode: "", fail_open: null },
  ])(
    "does not invent effective values for an incomplete report: %j",
    (nodePipeline) => {
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={vi.fn()}
          nodePipeline={nodePipeline}
        />,
      );
      expect(screen.getByRole("textbox", { name: "Mode" })).toHaveValue(
        "Not reported",
      );
      expect(screen.getByRole("textbox", { name: "Fail Open" })).toHaveValue(
        "Not reported",
      );
      expect(screen.queryByText(/this gateway's default/)).toBeNull();
    },
  );
});

describe("Guardrails off control reasons", () => {
  beforeEach(() => {
    role.orgLevel = 8;
  });
  it.each([8, 3])(
    "850(a): keeps Configure available subject only to org role %s",
    async (level) => {
      role.orgLevel = level;
      const user = userEvent.setup({ pointerEventsCheck: 0 });
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={vi.fn()}
          nodePipeline={{ enabled: false }}
        />,
      );
      const button = screen.getByRole("button", {
        name: "Configure Keyword Blocklist",
      });
      if (level === 8) {
        expect(button).toBeEnabled();
        await user.click(button);
        expect(await screen.findByRole("dialog")).toHaveTextContent(
          "Configure: Keyword Blocklist",
        );
      } else {
        expect(button).toBeDisabled();
        expect(
          screen.getByRole("checkbox", {
            name: "Turn Keyword Blocklist on or off",
          }),
        ).toBeDisabled();
        await user.hover(button.parentElement);
        expect(await screen.findByRole("tooltip")).toHaveTextContent(
          "Owner or Admin only",
        );
        expect(screen.getAllByRole("tooltip")).toHaveLength(1);
      }
    },
  );
});
