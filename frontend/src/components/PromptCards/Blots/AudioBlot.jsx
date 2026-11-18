import React from "react";
import "../PromptCardEditor.css";
import AudioEmbed from "../EmbedComponents/AudioEmbed";
import { AudioPlaybackProvider } from "src/components/custom-audio/context-provider/AudioPlaybackContext";
import MediaBlockEmbed from "./MediaBlockEmbed";

class AudioBlot extends MediaBlockEmbed {
  static renderCard(v, callbacks) {
    const canRemove =
      !callbacks.readOnly && typeof callbacks.handleRemoveAudio === "function";
    return (
      <AudioPlaybackProvider>
        <AudioEmbed
          url={v.url}
          name={v.name}
          size={v.size}
          isEmbed
          id={v.id}
          onDelete={
            canRemove ? () => callbacks.handleRemoveAudio(v.id) : undefined
          }
          mimeType={v.mimeType}
        />
      </AudioPlaybackProvider>
    );
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
AudioBlot.mediaKind = "audio";
AudioBlot.dataAttribute = "data-audio-data";

export default AudioBlot;
