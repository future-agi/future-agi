import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import PdfEmbed from "../EmbedComponents/PdfEmbed";
const BlockEmbed = Quill.import("blots/block/embed");

class PdfBlot extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    node.style.userSelect = "none";
    node.style.webkitUserSelect = "none";
    node.setAttribute("id", value.id);
    node.setAttribute(
      "data-pdf-data",
      JSON.stringify({
        url: value.url,
        pdf_name: value.name,
        pdf_size: value.size,
      }),
    );

    const root = createRoot(node);

    root.render(
      <PdfEmbed
        name={value.name}
        size={value.size}
        isEmbed
        id={value.id}
        onDelete={() => value.handleRemovePdf(value.id)}
      />,
    );

    return node;
  }

  static formats() {
    return null;
  }

  // Add value method to properly handle the blot's value
  static value(node) {
    const pdfDataAttr = node.getAttribute("data-pdf-data");
    // Return null if data attribute is missing or empty - prevents Quill from creating empty blots
    if (!pdfDataAttr || pdfDataAttr === "{}") {
      return null;
    }
    try {
      const pdfData = JSON.parse(pdfDataAttr);
      // Also return null if pdfData doesn't have a url (invalid blot)
      if (!pdfData.url) {
        return null;
      }
      return {
        id: node.getAttribute("id"),
        pdfData: pdfData,
      };
    } catch (e) {
      return null;
    }
  }
}

PdfBlot.blotName = "PdfBlot";
PdfBlot.tagName = "div";

export default PdfBlot;
