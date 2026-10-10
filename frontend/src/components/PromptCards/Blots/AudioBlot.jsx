import Quill from "quill";
import React from "react";
import { createRoot } from "react-dom/client";
import "../PromptCardEditor.css";
import AudioEmbed from "../EmbedComponents/AudioEmbed";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
const BlockEmbed = Quill.import("blots/block/embed");

class AudioBlot extends BlockEmbed {
  static create(value) {
    const node = super.create();
    node.setAttribute("contenteditable", false);
    node.style.userSelect = "none";
    node.style.webkitUserSelect = "none";
    node.setAttribute("id", value.id);
    node.setAttribute(
      "data-audio-data",
      JSON.stringify({
        url: value.url,
        audio_name: value.name,
        audio_size: value.size,
        audio_type: value.mimeType,
      }),
    );

    const root = createRoot(node);

    root.render(
      <AudioPlaybackProvider>
        <AudioEmbed
          url={value.url}
          name={value.name}
          size={value.size}
          isEmbed
          id={value.id}
          onDelete={() => value.handleRemoveAudio(value.id)}
          mimeType={value.mimeType}
        />
      </AudioPlaybackProvider>,
    );

    return node;
  }

  static formats() {
    return null;
  }

  // Add value method to properly handle the blot's value
  static value(node) {
    const audioDataAttr = node.getAttribute("data-audio-data");
    // Return null if data attribute is missing or empty - prevents Quill from creating empty blots
    if (!audioDataAttr || audioDataAttr === "{}") {
      return null;
    }
    try {
      const audioData = JSON.parse(audioDataAttr);
      // Also return null if audioData doesn't have a url (invalid blot)
      if (!audioData.url) {
        return null;
      }
      return {
        id: node.getAttribute("id"),
        audioData: audioData,
      };
    } catch (e) {
      return null;
    }
  }
}

AudioBlot.blotName = "AudioBlot";
AudioBlot.tagName = "div";

export default AudioBlot;
