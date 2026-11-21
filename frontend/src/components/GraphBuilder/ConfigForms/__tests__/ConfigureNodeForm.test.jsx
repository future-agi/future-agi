import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import ConfigureNodeForm from "../ConfigureNodeForm";
import { NODE_TYPES } from "../../store/graphStore";

vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldState: vi.fn(() => null),
}));
vi.mock("../ConfigureConversationNode", () => ({ default: () => null }));
vi.mock("../ConfigureEndCallNode", () => ({ default: () => null }));
vi.mock("../ConfigureTransferCallNode", () => ({ default: () => null }));

describe("ConfigureNodeForm node type change", () => {
  it("keeps the node in edit mode so its name can still be renamed", () => {
    const node = {
      id: "Greeting",
      type: NODE_TYPES.CONVERSATION,
      position: { x: 0, y: 0 },
      data: { name: "Greeting", prompt: "Hi", editMode: true },
    };
    const onChange = vi.fn();
    render(
      <ConfigureNodeForm node={node} onChange={onChange} onClose={vi.fn()} />,
    );

    const typeField = FormSearchSelectFieldState.mock.calls.at(-1)[0];
    typeField.onChange({ target: { value: NODE_TYPES.END } });

    expect(onChange).toHaveBeenCalledTimes(1);
    const [nodeId, updateNode] = onChange.mock.calls[0];
    expect(nodeId).toBe("Greeting");
    const updatedNode = updateNode(node);
    expect(updatedNode.type).toBe(NODE_TYPES.END);
    expect(updatedNode.data.editMode).toBe(true);
  });
});
