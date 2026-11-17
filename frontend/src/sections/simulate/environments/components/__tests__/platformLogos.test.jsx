import { createHash } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import PlatformLogo from "../PlatformLogo";
import { PLATFORM_LOGOS } from "../platformLogos";

const sha256 = (text) =>
  createHash("sha256").update(text, "utf8").digest("hex");

afterEach(cleanup);

// The team's icon for Bland (dev's
// frontend/src/assets/icons/platform-logos/bland.svg, Tabler Icons' outline
// infinity), not a vendor mark: its path and the file's bytes as dev has them.
const TEAM_BLAND_D =
  "M9.828 9.172a4 4 0 1 0 0 5.656a10 10 0 0 0 2.172 -2.828a10 10 0 0 1 2.172 -2.828a4 4 0 1 1 0 5.656a10 10 0 0 1 -2.172 -2.828a10 10 0 0 0 -2.172 -2.828";
const TEAM_BLAND_SHA256 =
  "fde51c100b8d9d615ff041eca087aa27e97c74f8bb2730240a9eb149742528ee";

// simple-icons@16.35.0 icons/elevenlabs.svg: its path verbatim, drawn in
// currentColor on its square viewBox, and nothing else changed.
const ELEVENLABS_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 24 24"><path d="M4.6035 0v24h4.9317V0zm9.8613 0v24h4.9317V0z"/></svg>\n';

describe("voice platform logos", () => {
  it("shows the team's icon for Bland, not a vendor favicon", () => {
    expect(sha256(PLATFORM_LOGOS.bland.svg)).toBe(TEAM_BLAND_SHA256);
    const { container } = render(<PlatformLogo id="bland" name="Bland.ai" />);
    const paths = container.querySelectorAll("svg path");
    expect(paths).toHaveLength(1);
    expect(paths[0].getAttribute("d")).toBe(TEAM_BLAND_D);
  });

  it("draws ElevenLabs with the simple-icons path, unaltered apart from colour", () => {
    expect(PLATFORM_LOGOS.elevenlabs.svg).toBe(ELEVENLABS_SVG);
    const { container } = render(
      <PlatformLogo id="elevenlabs" name="ElevenLabs" />,
    );
    expect(container.querySelector("svg path").getAttribute("d")).toBe(
      "M4.6035 0v24h4.9317V0zm9.8613 0v24h4.9317V0z",
    );
  });
});
