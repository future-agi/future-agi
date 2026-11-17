import React from "react";
import { describe, it, expect, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  within,
  waitFor,
} from "src/utils/test-utils";
import SecurityConfigTab from "./SecurityConfigTab";

vi.mock("@iconify/react", () => ({ Icon: () => null }));

const cases = [
  { kind: "IP", list: "allow", value: "10.0.0.0/8", field: "ipAcl" },
  { kind: "IP", list: "deny", value: "192.0.2.0/24", field: "ipAcl" },
  { kind: "Tool", list: "allow", value: "lookup", field: "toolPolicy" },
  { kind: "Tool", list: "deny", value: "execute", field: "toolPolicy" },
];

describe("release security draft-removal confirmations", () => {
  it.each(cases)(
    "confirms $kind $list removal without changing unrelated settings",
    async ({ kind, list, value, field }) => {
      const acl = {
        enabled: true,
        allow: ["10.0.0.0/8"],
        deny: ["192.0.2.0/24"],
      };
      const tools = {
        enabled: true,
        allow: ["lookup"],
        deny: ["execute"],
        max_tools_per_request: 7,
      };
      const ipChange = vi.fn();
      const toolChange = vi.fn();
      const privacyChange = vi.fn();
      render(
        <SecurityConfigTab
          ipAcl={acl}
          toolPolicy={tools}
          privacy={{ enabled: false }}
          onIpAclChange={ipChange}
          onToolPolicyChange={toolChange}
          onPrivacyChange={privacyChange}
        />,
      );
      const remove = () =>
        fireEvent.keyUp(screen.getByRole("button", { name: value }), {
          key: "Delete",
        });
      remove();
      expect(ipChange).not.toHaveBeenCalled();
      expect(toolChange).not.toHaveBeenCalled();
      let dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(`${kind} ${value} from ${list}`);
      expect(dialog).toHaveTextContent(
        "Click Save & Activate to apply the removal.",
      );
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(ipChange).not.toHaveBeenCalled();
      expect(toolChange).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: value })).toBeInTheDocument();

      remove();
      dialog = screen.getByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
      const change = field === "ipAcl" ? ipChange : toolChange;
      const untouched = field === "ipAcl" ? toolChange : ipChange;
      expect(change).toHaveBeenCalledTimes(1);
      expect(change).toHaveBeenCalledWith({
        ...(field === "ipAcl" ? acl : tools),
        [list]: [],
      });
      expect(untouched).not.toHaveBeenCalled();
      expect(privacyChange).not.toHaveBeenCalled();
    },
  );
});
