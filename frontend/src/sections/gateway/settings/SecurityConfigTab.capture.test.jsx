import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import SecurityConfigTab from "./SecurityConfigTab";

const access = vi.hoisted(() => ({ level: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: access.level }),
}));
afterEach(() => {
  access.level = 8;
});

const disclosure =
  "Full-content capture exports tool inputs and outputs to your organization's collector. It masks secret-named fields and recognized Bearer, sk-, AIza, AKIA/ASIA and JWT formats. Prefix-less Future AGI keys can remain in other text. Other sensitive content may still be exported. Off by default; only an organization Owner or Admin can enable it. Enabling and disabling are audited.";

describe("tool span capture consent", () => {
  it("refuses non-admin interaction", async () => {
    access.level = 3;
    const change = vi.fn();
    render(
      <SecurityConfigTab
        privacy={{}}
        onPrivacyChange={change}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("checkbox", {
      name: "Export full tool span content",
    });
    expect(toggle).toBeDisabled();
    await expect(userEvent.click(toggle)).rejects.toThrow(
      "pointer-events: none",
    );
    expect(change).not.toHaveBeenCalled();
  });
  it("normalizes an API camel-case consent when disabling", async () => {
    const change = vi.fn();
    render(
      <SecurityConfigTab
        privacy={{ traceContent: true, content_mode: "full" }}
        onPrivacyChange={change}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("checkbox", {
      name: "Export full tool span content",
    });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(change).toHaveBeenCalledWith({
      trace_content: false,
      content_mode: "full",
    });
  });
  it("defaults off and shows the disclosure beside the toggle", async () => {
    const change = vi.fn();
    render(
      <SecurityConfigTab
        privacy={{}}
        onPrivacyChange={change}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("checkbox", {
      name: "Export full tool span content",
    });
    expect(toggle).not.toBeChecked();
    expect(screen.getByText(disclosure)).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(change).toHaveBeenCalledWith({ trace_content: true });
  });
  it("disables capture without changing unrelated privacy settings", async () => {
    const change = vi.fn();
    render(
      <SecurityConfigTab
        privacy={{ trace_content: true, content_mode: "full" }}
        onPrivacyChange={change}
        onIpAclChange={vi.fn()}
        onToolPolicyChange={vi.fn()}
      />,
    );
    const toggle = screen.getByRole("checkbox", {
      name: "Export full tool span content",
    });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(change).toHaveBeenCalledWith({
      trace_content: false,
      content_mode: "full",
    });
  });
});
