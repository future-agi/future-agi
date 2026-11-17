// Where each mark the hosted voice list shows comes from. The voice platforms
// are not run-source catalog entries, so their rows live here, not in
// sections/common/RunSources/brandLogos.js.
//
//   source   where the bundled file was taken from
//   licence  the file's licence and the mark's owner
//   differs  how the bundled file differs from `source`
//   sha256   of the bundled file exactly as imported (?raw text). This is a
//            change detector, not proof of provenance: the test recomputes
//            it, so a file that changes fails until someone records a new
//            hash, and recording one is the moment to check the file against
//            its `source` again.
//
// The marks belong to their owners; we use a mark only to name the platform a
// user picks. Bland has no vendor mark here: it shows the team's generic icon.
const SI = "https://cdn.jsdelivr.net/npm/simple-icons@16.35.0/icons";

// Voice and chat Retell AI show the same file.
const RETELL = Object.freeze({
  source:
    "https://cdn.prod.website-files.com/64ada0f2685b2d18caa5e699/6af47328e980d936d902bbef_rh26-retell-logo.svg (the www.retellai.com header logo)",
  licence: "mark (c) Retell AI",
  differs: "wordmark removed, recoloured to currentColor, scaled 1/1.3",
  sha256: "63ee9a7a3a5c8ae54c0a7c98977cdde7ea07e95f8bd0e83c7153d4220b059f8a",
});

/** platform id → where its bundled mark comes from. */
export const VOICE_MARKS = Object.freeze({
  vapi: Object.freeze({
    source: "https://vapi.ai/icon.svg (the logomark on https://vapi.ai/brand)",
    licence: "mark (c) Vapi, Inc.",
    differs:
      "tile removed, recoloured to currentColor, cropped to a square viewBox",
    sha256: "24897229a7804970204e0c0d79922cb0da0fcde49f42ebcfca27a99c81556744",
  }),
  retell: RETELL,
  retell_chat: RETELL,
  livekit: Object.freeze({
    source: `${SI}/livekit.svg (the symbol of https://livekit.com/brand)`,
    licence: "simple-icons CC0; mark (c) LiveKit Incorporated",
    differs: "svgo path cleanup, recoloured to currentColor",
    sha256: "1ba932c0a5c933913a2448de28a60718f2799b28cffebdb27ab52d748472c118",
  }),
  bland: Object.freeze({
    source:
      "the team's file, not a vendor mark (origin/dev frontend/src/assets/icons/platform-logos/bland.svg; Tabler Icons infinity)",
    licence: "Tabler Icons MIT",
    differs:
      "dev's bytes, unchanged; against @tabler/icons@3.19.0 outline infinity.svg the path is the same, stroke-width 2.6 not 2, no size, class or frame path",
    sha256: "fde51c100b8d9d615ff041eca087aa27e97c74f8bb2730240a9eb149742528ee",
  }),
  elevenlabs: Object.freeze({
    source: `${SI}/elevenlabs.svg`,
    licence: "simple-icons CC0; mark (c) ElevenLabs, Inc.",
    differs: "recoloured to currentColor",
    sha256: "ab5640673a366fa9c181e14849119245d0866fdde4df771978596098a3d47a3b",
  }),
});
