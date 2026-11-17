import vapiLogo from "src/assets/brand-logos/vapi.svg?raw";
import retellLogo from "src/assets/brand-logos/retell.svg?raw";
import livekitLogo from "src/assets/brand-logos/livekit.svg?raw";
import blandLogo from "src/assets/brand-logos/bland.svg?raw";
import elevenlabsLogo from "src/assets/brand-logos/elevenlabs.svg?raw";

/* Marks for the hosted voice platforms, bundled in src/assets/brand-logos.
   Each file's source, licence, how it differs from that source and its
   sha256 are in VOICE_MARKS (./voiceMarks.js). Bland shows the team's
   generic icon, not a vendor mark. Inlined so a one-colour mark inherits
   `currentColor` (theme-aware); an optional `color` is the brand colour a
   mark is drawn in. Every logo is a symbol (`mark`), so
   the chip keeps the platform's name next to it. */
export const PLATFORM_LOGOS = {
  vapi: { svg: vapiLogo, type: "mark" },
  retell: { svg: retellLogo, type: "mark" },
  retell_chat: { svg: retellLogo, type: "mark" },
  livekit: { svg: livekitLogo, type: "mark" },
  bland: { svg: blandLogo, type: "mark" },
  elevenlabs: { svg: elevenlabsLogo, type: "mark" },
};
