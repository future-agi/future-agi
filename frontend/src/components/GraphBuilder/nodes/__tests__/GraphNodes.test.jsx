import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ConversationNode from "../ConversationNode";
import EndCallNode from "../EndCallNode";
import EndChatNode from "../EndChatNode";
import TransferCallNode from "../TransferCallNode";
import TransferChatNode from "../TransferChatNode";
import { useGraphStore } from "../../store/graphStore";

// These nodes render outside a React Flow canvas, so drop their handles.
vi.mock("@xyflow/react", async (importOriginal) => ({
  ...(await importOriginal()),
  Handle: () => null,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// jsdom never applies :hover, so read the stylesheet instead: is there an
// "A:hover B { opacity: 1 }" rule that matches the element once ":hover" is
// dropped from its selector?
const isRevealedOnHover = (element) =>
  Array.from(document.styleSheets).some((sheet) =>
    Array.from(sheet.cssRules).some((rule) => {
      if (!rule.selectorText?.includes(":hover")) return false;
      if (rule.style?.opacity !== "1") return false;
      try {
        return element.matches(rule.selectorText.replaceAll(":hover", ""));
      } catch {
        return false;
      }
    }),
  );

const NODE_COMPONENTS = [
  ["conversation", ConversationNode],
  ["end call", EndCallNode],
  ["end chat", EndChatNode],
  ["transfer call", TransferCallNode],
  ["transfer chat", TransferChatNode],
];

describe("Graph builder nodes", () => {
  it.each(NODE_COMPONENTS)(
    "%s node shows its delete and duplicate buttons on hover after a multiword rename",
    (_label, NodeComponent) => {
      // renameNode makes the new name the node id, spaces included.
      const { container } = render(
        <NodeComponent
          id="Customer Greeting"
          data={{ name: "Customer Greeting", prompt: "Hi", editMode: true }}
          isConnectable
        />,
      );
      const buttons = container.querySelectorAll("button");
      const duplicateButton = buttons[buttons.length - 1];
      const actions = duplicateButton.parentElement;

      expect(isRevealedOnHover(actions)).toBe(true);
      expect(isRevealedOnHover(duplicateButton)).toBe(true);
      expect(duplicateButton).toHaveAccessibleName("Duplicate node");
      expect(
        screen.getByRole("button", { name: "Delete node" }),
      ).toBeInTheDocument();
    },
  );

  it("lets the first node of a new workflow be renamed", () => {
    useGraphStore.getState().resetGraph();
    const [startNode] = useGraphStore.getState().nodes;
    expect(startNode.data.editMode).toBe(true);

    render(
      <ConversationNode
        id={startNode.id}
        data={startNode.data}
        isConnectable
      />,
    );
    expect(
      screen.getByRole("button", { name: `Rename ${startNode.data.name}` }),
    ).toBeInTheDocument();
  });
});
