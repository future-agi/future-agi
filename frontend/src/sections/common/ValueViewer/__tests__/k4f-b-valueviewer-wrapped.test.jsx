// integration (RV-02, closing-merge patch): a wrapped conversation — an object that
// holds its messages in `turns` / `messages` next to other keys — reads as
// chat, but JSON, YAML, Tree and Pretty show it as stored: every key
// (`resolution`, `failing_turn`, `channel`) and the stored roles
// (`customer` / `agent`), and Tree's paths start at `turns`. Chat still
// renders the turns as a conversation.
import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";
import ValueViewer from "../ValueViewer";

const session = {
  turns: [
    { n: 1, role: "customer", content: "Locked out since the SSO change." },
    { n: 2, role: "agent", content: "Try resetting your password." },
  ],
  resolution: "Fail",
  resolution_reason: "Needed an escalation to enterprise-support (P1).",
  failing_turn: 2,
  channel: "chat",
};
const KEYS = ["turns", "resolution", "failing_turn", "channel"];

const view = (value, mode) =>
  render(<ValueViewer value={value} view={mode} toolbar={false} />).container
    .textContent;

describe("ValueViewer · a wrapped conversation keeps its keys and roles", () => {
  it.each([
    ["the stored object", session],
    ["its JSON text", JSON.stringify(session)],
  ])("%s: JSON and YAML print every key and the stored roles", (_, value) => {
    const json = view(value, "json");
    KEYS.forEach((k) => expect(json).toContain(`"${k}"`));
    expect(json).toContain('"customer"');
    expect(json).toContain('"agent"');
    expect(json).not.toContain('"assistant"');
    expect(JSON.parse(json)).toEqual(session);
    const yaml = view(value, "yaml");
    KEYS.forEach((k) => expect(yaml).toContain(`${k}:`));
    expect(yaml).toContain("role: customer");
    expect(yaml).toContain("role: agent");
    expect(yaml).not.toContain("role: user");
  });

  it("Tree and Pretty list the stored keys, not the message list", () => {
    const tree = view(session, "tree");
    KEYS.forEach((k) => expect(tree).toContain(k));
    expect(tree).toContain("customer");
    const pretty = view(session, "pretty");
    KEYS.forEach((k) => expect(pretty).toContain(k));
    expect(pretty).not.toContain("[0]");
  });

  it("Chat still renders the turns as a conversation", () => {
    const chat = view(session, "chat");
    expect(chat).toContain("Locked out since the SSO change.");
    expect(chat).toContain("Try resetting your password.");
  });
});
