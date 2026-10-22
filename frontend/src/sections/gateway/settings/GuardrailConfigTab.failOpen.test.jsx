// The Guardrails tab shows how this gateway runs the org's checks (GET
// /-/config guardrail_pipeline, passed through by the config action)
// read-only, with the reason, and never edits the org's stored fail_open or
// pipeline_mode.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

const STORED = { fail_open: true, pipeline_mode: "parallel", checks: {} };

function failOpenSwitch() {
  return screen.getByRole("checkbox", { name: "Fail Open" });
}

describe("Guardrails tab: Fail Open and Mode", () => {
  it("shows the gateway's values read-only, with the reason", () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={STORED}
        onChange={onChange}
        nodePipeline={{ enabled: true, fail_open: false, mode: "sequential" }}
      />,
    );
    expect(failOpenSwitch()).toBeDisabled();
    expect(failOpenSwitch()).not.toBeChecked();
    const mode = screen.getByRole("combobox", { name: "Mode" });
    expect(mode).toHaveAttribute("aria-disabled", "true");
    expect(mode).toHaveTextContent("Sequential");
    expect(
      screen.getByText(
        "Set by this gateway, read-only: it runs your checks in turn and stops at the first block, and a check that errors or times out blocks the request. This organization's own Mode and Fail Open are saved and not applied.",
      ),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says a check that errors lets the request through when the gateway fails open", () => {
    render(
      <GuardrailConfigTab
        guardrails={STORED}
        onChange={vi.fn()}
        nodePipeline={{ enabled: true, fail_open: true, mode: "sequential" }}
      />,
    );
    expect(failOpenSwitch()).toBeChecked();
    expect(
      screen.getByText(
        /a check that errors or times out lets the request through/,
      ),
    ).toBeInTheDocument();
  });

  it("says when guardrails are off on this gateway", () => {
    render(
      <GuardrailConfigTab
        guardrails={STORED}
        onChange={vi.fn()}
        nodePipeline={{ enabled: false, fail_open: false, mode: "sequential" }}
      />,
    );
    expect(
      screen.getByText(
        "Set by this gateway, read-only: guardrails are off on this gateway, so no check runs. This organization's own Mode and Fail Open are saved and not applied.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/runs your checks in turn/)).toBeNull();
  });

  it("is read-only and says so when the gateway does not report it", () => {
    render(<GuardrailConfigTab guardrails={STORED} onChange={vi.fn()} />);
    expect(failOpenSwitch()).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Mode" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      screen.getByText(
        "Set by this gateway, read-only: this gateway does not report how it runs organization checks. This organization's own Mode and Fail Open are saved and not applied.",
      ),
    ).toBeInTheDocument();
  });
});
