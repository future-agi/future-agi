import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "src/utils/test-utils";

const captured = { onAudioError: null };

vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: (props) => {
    captured.onAudioError = props.onAudioError;
    return <div data-testid="custom-audio-player" />;
  },
}));

import AudioEmbed from "../AudioEmbed";

const URL_A = "https://example.com/one.mp3";
const URL_B = "https://example.com/two.mp3";

const renderEmbed = (url) =>
  render(
    <AudioEmbed url={url} name="Audio 1" size={1024} id="audio-0" isEmbed />,
  );

describe("AudioEmbed CORS fallback", () => {
  it("renders the WaveSurfer player until the fetch fails", () => {
    renderEmbed(URL_A);

    expect(screen.getByTestId("custom-audio-player")).toBeTruthy();
    expect(document.querySelector("audio")).toBeNull();
  });

  it("falls back to a native player on a fetch TypeError (CORS) and resets per URL", () => {
    const { rerender } = renderEmbed(URL_A);

    act(() => {
      captured.onAudioError(new TypeError("Failed to fetch"));
    });

    const audio = document.querySelector("audio");
    expect(audio).toBeTruthy();
    expect(audio.src).toContain(URL_A);
    expect(audio.getAttribute("preload")).toBe("metadata");
    expect(screen.queryByTestId("custom-audio-player")).toBeNull();

    rerender(
      <AudioEmbed
        url={URL_B}
        name="Audio 1"
        size={1024}
        id="audio-0"
        isEmbed
      />,
    );
    expect(document.querySelector("audio")).toBeNull();
    expect(screen.getByTestId("custom-audio-player")).toBeTruthy();
  });

  it("keeps the player for real HTTP errors so its error text stays visible", () => {
    renderEmbed(URL_A);

    captured.onAudioError(
      new Error(`Failed to fetch ${URL_A}: 404 (Not Found)`),
    );

    expect(screen.getByTestId("custom-audio-player")).toBeTruthy();
    expect(document.querySelector("audio")).toBeNull();
  });
});
