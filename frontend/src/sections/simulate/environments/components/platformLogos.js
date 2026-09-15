import vapiLogo from "src/assets/icons/platform-logos/vapi.svg?raw";
import retellLogo from "src/assets/icons/platform-logos/retell.svg?raw";
import blandLogo from "src/assets/icons/platform-logos/bland.svg?raw";
import elevenlabsLogo from "src/assets/icons/platform-logos/elevenlabs.svg?raw";
import livekitLogo from "src/assets/icons/platform-logos/livekit.svg?raw";

/* Official brand marks for the hosted voice platforms, inlined so they inherit
   `currentColor` (theme-aware). Each vendor's own published logo, used to
   identify its integration — the same way the app labels its other service
   integrations. `wordmark` logos already contain the brand name (so the chip
   drops its text label); `mark` logos are a symbol only (chip keeps the name). */
export const PLATFORM_LOGOS = {
  vapi: { svg: vapiLogo, type: "wordmark", maxWidth: 46 },
  retell: { svg: retellLogo, type: "wordmark", maxWidth: 54 },
  bland: { svg: blandLogo, type: "mark" },
  elevenlabs: { svg: elevenlabsLogo, type: "mark" },
  livekit: { svg: livekitLogo, type: "mark" },
};
