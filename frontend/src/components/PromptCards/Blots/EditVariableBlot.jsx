import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import EditVariable from "../EmbedComponents/EditVariable";
const BlockEmbed = Quill.import("blots/embed");

class EditVariableBolt extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    if (value.fromBlock) {
      node.setAttribute("data-from-block", "true");
    }
    // Render React component
    const root = createRoot(node);

    root.render(
      <EditVariable
        openVariableEditor={value.openVariableEditor}
        fromBlock={!!value.fromBlock}
      />,
    );

    return node;
  }

  // Add value method to properly handle the blot's value
  static value(node) {
    return {
      id: node.getAttribute("id"),
      fromBlock: node.getAttribute("data-from-block") === "true",
    };
  }
}

EditVariableBolt.blotName = "EditVariable";
EditVariableBolt.tagName = "span";
EditVariableBolt.className = "fi-edit-variable-class"; // add this line if tagName is span

export default EditVariableBolt;
