import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import EditVariable from "../EmbedComponents/EditVariable";
import { getEmbedCallbacks } from "./embedCallbacks";
const BlockEmbed = Quill.import("blots/embed");

const VALUE_KEY = "__promptEditVariableValue";

class EditVariableBolt extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    if (value.fromBlock) {
      node.setAttribute("data-from-block", "true");
    }
    // The React control is rendered in attach(): value() cannot carry
    // openVariableEditor, so history replay / paste resolve it from the owning
    // editor's callback registry.
    node[VALUE_KEY] = {
      fromBlock: !!value.fromBlock,
      openVariableEditor: value.openVariableEditor,
    };
    return node;
  }

  attach() {
    super.attach();
    const v = this.domNode[VALUE_KEY] || {};
    const open =
      getEmbedCallbacks(this).openVariableEditor ?? v.openVariableEditor;
    if (!this.reactRoot) {
      this.reactRoot = createRoot(this.domNode);
    }
    this.reactRoot.render(
      <EditVariable openVariableEditor={open} fromBlock={!!v.fromBlock} />,
    );
  }

  detach() {
    this.unmountCard();
    super.detach();
  }

  /** Unmounts the React chip without touching the Quill document. */
  unmountCard() {
    const root = this.reactRoot;
    this.reactRoot = null;
    if (root) {
      queueMicrotask(() => root.unmount());
    }
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
