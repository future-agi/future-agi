import { describe, expect, it } from "vitest";

import { getUploadSourceItems, mapUploadedMedia } from "./uploadMediaMapping";

describe("upload media response mapping", () => {
  it("uses canonical snake_case file names from upload-file responses", () => {
    const mapped = mapUploadedMedia({
      type: "pdf",
      uploadedUrl: [
        {
          url: "http://localhost:9005/fi-content-dev/tempcust/report.pdf",
          file_name: "report.pdf",
        },
      ],
      sourceItems: [{ name: "local-name.pdf", size: 123 }],
    });

    expect(mapped).toEqual([
      {
        url: "http://localhost:9005/fi-content-dev/tempcust/report.pdf",
        pdf_name: "report.pdf",
        pdf_size: 123,
      },
    ]);
  });

  it("maps link uploads from links when files is an empty array", () => {
    const links = [{ name: "Image 1", url: "https://example.test/source" }];
    const sourceItems = getUploadSourceItems({ files: [], links });
    const mapped = mapUploadedMedia({
      type: "image",
      uploadedUrl: [
        {
          url: "http://localhost:9005/fi-content-dev/tempcust/generated.png",
          file_name: "source.png",
        },
      ],
      sourceItems,
    });

    expect(sourceItems).toBe(links);
    expect(mapped[0]).toMatchObject({
      img_name: "source.png",
      img_size: undefined,
    });
  });

  it("keeps legacy camelCase response compatibility", () => {
    const mapped = mapUploadedMedia({
      type: "audio",
      uploadedUrl: [
        {
          url: "http://localhost:9005/fi-content-dev/tempcust/audio.mp3",
          fileName: "audio.mp3",
        },
      ],
      sourceItems: [{ name: "fallback.wav", size: 456, type: "audio/wav" }],
    });

    expect(mapped).toEqual([
      {
        url: "http://localhost:9005/fi-content-dev/tempcust/audio.mp3",
        audio_name: "audio.mp3",
        audio_size: 456,
        audio_type: "audio/wav",
      },
    ]);
  });
});
