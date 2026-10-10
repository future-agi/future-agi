import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import ImageEmbed from "../EmbedComponents/ImageEmbed";
const BlockEmbed = Quill.import("blots/block/embed");

class ImageBlot extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    node.style.userSelect = "none";
    node.style.webkitUserSelect = "none";
    node.setAttribute("id", value.id);
    node.setAttribute(
      "data-image-data",
      JSON.stringify({
        url: value.url,
        img_name: value.name,
        img_size: value.size,
      }),
    );

    const root = createRoot(node);

    root.render(
      <ImageEmbed
        url={value.url}
        name={value.name}
        size={value.size}
        onMagnify={() =>
          value.setSelectedImage({
            url: value.url,
            name: value.name,
            size: value.size,
            id: value.id,
          })
        }
        isEmbed
        id={value.id}
        onDelete={() => value.handleRemoveImage(value.id)}
        onReplace={() =>
          value.setSelectedImage({
            url: value.url,
            name: value.name,
            size: value.size,
            id: value.id,
            replace: true,
          })
        }
      />,
    );

    return node;
  }

  static formats() {
    return null;
  }

  // Add value method to properly handle the blot's value
  static value(node) {
    const imageDataAttr = node.getAttribute("data-image-data");
    // Return null if data attribute is missing or empty - prevents Quill from creating empty blots
    if (!imageDataAttr || imageDataAttr === "{}") {
      return null;
    }
    try {
      const imageData = JSON.parse(imageDataAttr);
      // Also return null if imageData doesn't have a url (invalid blot)
      if (!imageData.url) {
        return null;
      }
      return {
        id: node.getAttribute("id"),
        imageData: imageData,
      };
    } catch (e) {
      return null;
    }
  }
}

ImageBlot.blotName = "ImageBlot";
ImageBlot.tagName = "div";

export default ImageBlot;
