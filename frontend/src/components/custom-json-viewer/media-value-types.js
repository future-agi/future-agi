import { defineDataType } from "@textea/json-viewer";
import { getMediaKind } from "./media-detection";
import { AudioPreview, ImagePreview } from "./media-preview";

const imageType = defineDataType({
  is: (value) => getMediaKind(value) === "image",
  Component: ImagePreview,
});

const audioType = defineDataType({
  is: (value) => getMediaKind(value) === "audio",
  Component: AudioPreview,
});

// Shared by every JSON viewer. Kept at module level so JsonViewer doesn't
// re-register its value types on each render.
export const mediaValueTypes = [imageType, audioType];
