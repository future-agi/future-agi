import { describe, expect, it } from "vitest";
import { isAudioAttrPath, isImageAttrPath } from "../extractMediaSrc";

const contentPath = "llm.input_messages.0.message.contents.0.message_content";

describe("isImageAttrPath canonical image URLs (OBS-SW-29)", () => {
  it("recognizes the canonical gateway input image URL attribute", () => {
    expect(
      isImageAttrPath(
        "llm.input_messages.0.message.contents.0.message_content.image.url",
      ),
    ).toBe(true);
  });

  it("recognizes canonical output image URL attributes", () => {
    expect(
      isImageAttrPath(
        "llm.output_messages.2.message.contents.3.message_content.image.url",
      ),
    ).toBe(true);
  });

  it("recognizes the canonical image URL suffix case-insensitively", () => {
    expect(isImageAttrPath(`${contentPath}.IMAGE.URL`)).toBe(true);
  });

  it("preserves both legacy image suffixes and case-insensitive matching", () => {
    for (const suffix of ["image", "image_url.url", "IMAGE", "IMAGE_URL.URL"]) {
      expect(isImageAttrPath(`${contentPath}.${suffix}`)).toBe(true);
    }
  });

  it("requires an exact terminal dotted image suffix", () => {
    for (const path of [
      "",
      "image.url",
      `${contentPath}.imageXurl`,
      `${contentPath}.notimage.url`,
      `${contentPath}.image_url`,
      `${contentPath}.image.url.extra`,
      `${contentPath}.image.urlx`,
      `${contentPath}.image.url?size=small`,
      `${contentPath}.text`,
      `${contentPath}.audio`,
      `${contentPath}.audio_content.url`,
    ]) {
      expect(isImageAttrPath(path)).toBe(false);
    }
  });

  it("rejects non-string paths without coercion or exceptions", () => {
    for (const path of [
      undefined,
      null,
      false,
      0,
      {},
      [],
      [`${contentPath}.image.url`],
      { toString: () => `${contentPath}.image.url` },
    ]) {
      expect(isImageAttrPath(path)).toBe(false);
      expect(isAudioAttrPath(path)).toBe(false);
    }
  });

  it("preserves audio suffixes without treating image URLs as audio", () => {
    for (const suffix of [
      "audio",
      "audio_content.url",
      "AUDIO",
      "AUDIO_CONTENT.URL",
    ]) {
      expect(isAudioAttrPath(`${contentPath}.${suffix}`)).toBe(true);
    }
    for (const suffix of [
      "image",
      "image.url",
      "IMAGE.URL",
      "image_url.url",
      "text",
    ]) {
      expect(isAudioAttrPath(`${contentPath}.${suffix}`)).toBe(false);
    }
  });
});
