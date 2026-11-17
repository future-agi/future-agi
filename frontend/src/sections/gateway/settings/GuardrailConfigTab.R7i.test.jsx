// Mode and Fail Open are the organization's own settings, which the gateway
// applies: editable controls showing the stored values, whatever the gateway
// node reports.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailConfigTab from "./GuardrailConfigTab";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

const role = vi.hoisted(() => ({ orgLevel: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => role,
}));
const STORED = { fail_open: true, pipeline_mode: "parallel", checks: {} };

describe("917: the organization's Fail Open and Mode", () => {
  it.each([true, false])(
    "keeps both controls visible and editable when enabled=%s",
    async (enabled) => {
      const onChange = vi.fn();
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={onChange}
          nodePipeline={{ enabled, fail_open: false, mode: "sequential" }}
        />,
      );
      const mode = screen.getByRole("combobox", { name: "Mode" });
      const failOpen = screen.getByRole("checkbox", { name: "Fail Open" });
      expect(mode).toHaveTextContent("Parallel");
      expect(failOpen).toBeChecked();
      expect(failOpen).toBeEnabled();
      await userEvent.click(failOpen);
      expect(onChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ fail_open: false }),
      );
      expect(
        screen.queryByText(
          "Mode and Fail Open are set by this gateway and are read-only here.",
        ),
      ).toBeNull();
      expect(
        screen.getAllByText(
          "Fail Open never applies to a check that rewrites the request: if one does not finish, the request is refused.",
        ),
      ).toHaveLength(1);
    },
  );

  it("shows the stored fail-open value, not the value the node reports", () => {
    render(
      <GuardrailConfigTab
        guardrails={{ ...STORED, fail_open: false }}
        onChange={vi.fn()}
        nodePipeline={{ enabled: true, fail_open: true, mode: "sequential" }}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: "Fail Open" }),
    ).not.toBeChecked();
  });

  describe.each([true, false])("with guardrails enabled=%s", (enabled) => {
    it.each([true, false])(
      "shows stored Fail Open=%s once, without the node-default sentence",
      (failOpen) => {
        render(
          <GuardrailConfigTab
            guardrails={{ ...STORED, fail_open: failOpen }}
            onChange={vi.fn()}
            nodePipeline={{ enabled, fail_open: !failOpen, mode: "sequential" }}
          />,
        );
        const boxes = screen.getAllByRole("checkbox", { name: "Fail Open" });
        expect(boxes).toHaveLength(1);
        expect(boxes[0].checked).toBe(failOpen);
        expect(screen.queryByText(/this gateway's default/)).toBeNull();
      },
    );
  });

  it.each([
    undefined,
    null,
    {},
    { enabled: true },
    { mode: "", fail_open: null },
  ])(
    "reads the stored values whatever the node reports: %j",
    (nodePipeline) => {
      render(
        <GuardrailConfigTab
          guardrails={STORED}
          onChange={vi.fn()}
          nodePipeline={nodePipeline}
        />,
      );
      expect(screen.getByRole("combobox", { name: "Mode" })).toHaveTextContent(
        "Parallel",
      );
      expect(screen.getByRole("checkbox", { name: "Fail Open" })).toBeChecked();
      expect(screen.queryByRole("textbox", { name: "Fail Open" })).toBeNull();
    },
  );
});

describe("an organization that has not set Fail Open or Mode", () => {
  const FAIL_OPEN_CAPTION =
    "Not set for this organization. The gateway's own Fail Open setting applies (on by default).";
  const MODE_CAPTION =
    "Not set for this organization. Checks run one at a time, and the first check that blocks stops the rest.";

  it.each([
    [{ enabled: true, fail_open: true }, true],
    [{ enabled: true, fail_open: false }, false],
    [undefined, true],
  ])(
    "shows the gateway's Fail Open position with the caption, node %j",
    (nodePipeline, on) => {
      render(
        <GuardrailConfigTab
          guardrails={{ checks: {} }}
          onChange={vi.fn()}
          nodePipeline={nodePipeline}
        />,
      );
      const box = screen.getByRole("checkbox", { name: "Fail Open" });
      expect(box.checked).toBe(on);
      expect(screen.getAllByText(FAIL_OPEN_CAPTION)).toHaveLength(1);
      expect(screen.getByRole("combobox", { name: "Mode" })).toHaveTextContent(
        "Not set",
      );
      expect(screen.getAllByText(MODE_CAPTION)).toHaveLength(1);
    },
  );

  it("writes Fail Open on a toggle, and the caption goes", async () => {
    const onChange = vi.fn();
    const node = { enabled: true, fail_open: true };
    const view = render(
      <GuardrailConfigTab
        guardrails={{ checks: {} }}
        onChange={onChange}
        nodePipeline={node}
      />,
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Fail Open" }));
    expect(onChange).toHaveBeenLastCalledWith({ checks: {}, fail_open: false });
    view.rerender(
      <GuardrailConfigTab
        guardrails={onChange.mock.lastCall[0]}
        onChange={onChange}
        nodePipeline={node}
      />,
    );
    expect(screen.queryByText(FAIL_OPEN_CAPTION)).toBeNull();
    expect(
      screen.getByRole("checkbox", { name: "Fail Open" }),
    ).not.toBeChecked();
  });

  it.each([true, false])(
    "shows a stored Fail Open=%s as stored, with no caption",
    (stored) => {
      render(
        <GuardrailConfigTab
          guardrails={{
            checks: {},
            fail_open: stored,
            pipeline_mode: "parallel",
          }}
          onChange={vi.fn()}
          nodePipeline={{ enabled: true, fail_open: !stored }}
        />,
      );
      const box = screen.getByRole("checkbox", { name: "Fail Open" });
      expect(box.checked).toBe(stored);
      expect(screen.queryByText(FAIL_OPEN_CAPTION)).toBeNull();
      expect(screen.queryByText(MODE_CAPTION)).toBeNull();
    },
  );

  it("saves another guardrail setting with no fail_open and no pipeline_mode", () => {
    const onChange = vi.fn();
    const saved = { checks: {} };
    render(
      <GuardrailConfigTab
        guardrails={saved}
        saved={saved}
        onChange={onChange}
        nodePipeline={{ enabled: true, fail_open: true }}
      />,
    );
    fireEvent.change(screen.getByLabelText("Timeout (ms)"), {
      target: { value: "3000" },
    });
    // The update-config body the Org config editor sends for this draft.
    const body = buildConfigPatch(
      { guardrails: saved },
      { guardrails: guardrailsForSave(onChange.mock.lastCall[0], saved) },
      ["guardrails"],
    );
    expect(body.guardrails).toMatchObject({ timeout_ms: 3000 });
    for (const key of [
      "fail_open",
      "failOpen",
      "pipeline_mode",
      "pipelineMode",
    ]) {
      expect(body.guardrails).not.toHaveProperty(key);
    }
  });
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
