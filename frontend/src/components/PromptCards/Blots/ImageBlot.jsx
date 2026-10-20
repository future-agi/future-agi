import React from "react";
import "../PromptCardEditor.css";
import ImageEmbed from "../EmbedComponents/ImageEmbed";
import MediaBlockEmbed from "./MediaBlockEmbed";

class ImageBlot extends MediaBlockEmbed {
  static renderCard(v, callbacks) {
    const readOnly = Boolean(callbacks.readOnly);
    const info = { url: v.url, name: v.name, size: v.size, id: v.id };
    const canSelect = typeof callbacks.setSelectedImage === "function";
    const canRemove =
      !readOnly && typeof callbacks.handleRemoveImage === "function";
    return (
      <ImageEmbed
        url={v.url}
        name={v.name}
        size={v.size}
        onMagnify={
          canSelect ? () => callbacks.setSelectedImage(info) : undefined
        }
        isEmbed
        id={v.id}
        onDelete={
          canRemove ? () => callbacks.handleRemoveImage(v.id) : undefined
        }
        onReplace={
          canSelect && !readOnly
            ? () => callbacks.setSelectedImage({ ...info, replace: true })
            : undefined
        }
      />
    );
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
ImageBlot.mediaKind = "image";
ImageBlot.dataAttribute = "data-image-data";

export default ImageBlot;
