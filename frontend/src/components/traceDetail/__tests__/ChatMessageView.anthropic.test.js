import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ChatMessageView from "../ChatMessageView";

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const encodings = [
  { label: "decoded", encode: (value) => value },
  { label: "JSON-encoded", encode: JSON.stringify },
];
const systemText = "You are support.";
const question = "Why was I charged?";
// The Anthropic Messages request pinned by insights/tests/test_evidence.py.
const request = (system) => ({
  max_tokens: 1024,
  system,
  messages: [
    { role: "user", content: question },
    {
      role: "assistant",
      content: [
        { type: "thinking", thinking: "Check billing." },
        {
          type: "tool_use",
          id: "tu_1",
          name: "billing",
          input: { account: 7 },
        },
      ],
    },
    {
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: "tu_1",
          is_error: true,
          content: [{ type: "text", text: "timeout" }],
        },
      ],
    },
  ],
});
const view = (props) => render(createElement(ChatMessageView, props));

// Real Markdown, message cards, tool cards and theme providers; only icons
// are stubbed.
describe("ChatMessageView Anthropic messages", () => {
  for (const system of [
    { label: "string", value: systemText },
    { label: "text block", value: [{ type: "text", text: systemText }] },
  ]) {
    it.each(encodings)(
      `OBS-SW-140 shows the ${system.label} system prompt of a $label request first`,
      ({ encode }) => {
        const { container } = view({ input: encode(request(system.value)) });

        expect(screen.getByText("system")).toBeInTheDocument();
        expect(screen.getAllByText(systemText)).toHaveLength(1);
        const text = container.textContent;
        expect(text.indexOf(systemText)).toBeLessThan(text.indexOf(question));
      },
    );
  }

  it.each(encodings)(
    "OBS-SW-140 shows $label thinking, tool_use and tool_result blocks without JSON",
    ({ encode }) => {
      const { container } = view({ input: encode(request(systemText)) });

      expect(screen.getByText("Check billing.")).toBeInTheDocument();
      fireEvent.click(screen.getByText("billing"));
      const text = container.textContent;
      expect(text).toContain('"account": 7');
      expect(screen.getByText("tu_1")).toBeInTheDocument();
      expect(screen.getByText("timeout")).toBeInTheDocument();
      expect(text).not.toContain('"type":"thinking"');
      expect(text).not.toContain('"type":"tool_use"');
      expect(text).not.toContain('"type":"tool_result"');
      expect(text).not.toContain("(empty)");
    },
  );

  it.each(encodings)(
    "OBS-SW-140 shows a $label tool_use reply as a tool card",
    ({ encode }) => {
      const { container } = view({
        input: encode({ messages: [{ role: "user", content: question }] }),
        output: encode({
          id: "msg_1",
          type: "message",
          role: "assistant",
          content: [
            { type: "text", text: "Issuing a refund." },
            {
              type: "tool_use",
              id: "tu_2",
              name: "refund",
              input: { amount: 5 },
            },
          ],
          stop_reason: "tool_use",
        }),
      });

      expect(screen.getByText("Issuing a refund.")).toBeInTheDocument();
      fireEvent.click(screen.getByText("refund"));
      expect(container.textContent).toContain('"amount": 5');
      expect(container.textContent).not.toContain('"type":"tool_use"');
    },
  );

  it("OBS-SW-140 keeps an attribute system turn when the request has none", () => {
    const { container } = view({
      input: { messages: [{ role: "user", content: question }] },
      attrInputMessages: [
        { role: "system", content: [systemText] },
        { role: "user", content: [question] },
      ],
    });

    expect(screen.getAllByText(systemText)).toHaveLength(1);
    expect(screen.getAllByText(question)).toHaveLength(1);
    const text = container.textContent;
    expect(text.indexOf(systemText)).toBeLessThan(text.indexOf(question));
  });

  it("keeps a captured system message without adding the request system", () => {
    view({
      input: {
        system: "Outer system prompt",
        messages: [
          { role: "system", content: "Captured system prompt" },
          { role: "user", content: question },
        ],
      },
    });

    expect(screen.getAllByText("Captured system prompt")).toHaveLength(1);
    expect(screen.queryByText("Outer system prompt")).not.toBeInTheDocument();
  });
});
