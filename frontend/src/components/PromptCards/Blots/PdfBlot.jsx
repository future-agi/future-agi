import React from "react";
import "../PromptCardEditor.css";
import PdfEmbed from "../EmbedComponents/PdfEmbed";
import MediaBlockEmbed from "./MediaBlockEmbed";

class PdfBlot extends MediaBlockEmbed {
  static renderCard(v, callbacks) {
    const canRemove =
      !callbacks.readOnly && typeof callbacks.handleRemovePdf === "function";
    return (
      <PdfEmbed
        name={v.name}
        size={v.size}
        isEmbed
        id={v.id}
        onDelete={canRemove ? () => callbacks.handleRemovePdf(v.id) : undefined}
      />
    );
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
PdfBlot.mediaKind = "pdf";
PdfBlot.dataAttribute = "data-pdf-data";

export default PdfBlot;
