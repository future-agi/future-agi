import vapiLogo from "src/assets/brand-logos/vapi.svg?raw";
import retellLogo from "src/assets/brand-logos/retell.svg?raw";
import livekitLogo from "src/assets/brand-logos/livekit.svg?raw";

/* Official brand marks for the hosted voice platforms: the same bundled files
   the experiment run-source catalog shows (src/assets/brand-logos; source and
   licence of each in sections/common/RunSources/brandLogos.js), so a brand
   has one mark across the product. Inlined so a one-colour mark inherits
   `currentColor` (theme-aware); an optional `color` is the brand colour a
   mark is drawn in. Every logo is a symbol (`mark`), so
   the chip keeps the platform's name next to it. */
export const PLATFORM_LOGOS = {
  vapi: { svg: vapiLogo, type: "mark" },
  retell: { svg: retellLogo, type: "mark" },
  retell_chat: { svg: retellLogo, type: "mark" },
  livekit: { svg: livekitLogo, type: "mark" },
};
