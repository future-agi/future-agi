// The class on every element that can hold a personal API key in full (the
// snippet once the key is revealed, the key strip, Copy's fallback textarea),
// so no session recording ever carries it (never
// logged). PostHog's replay records text and input values on this app's
// pages (maskAllInputs false, no text mask, utils/PostHog/posthog.js) but
// skips `ph-no-capture`; Sentry's replay blocks both classes
// (SENTRY_REPLAY_BLOCK_SELECTORS); Mixpanel's recorder blocks both by its
// default record_block_class. The Omega frame carries the same classes
// (embed/frame-attributes.js).

import { FRAME_CLASS } from "src/sections/omega/embed/frame-attributes";

export const NO_RECORDING_CLASS = FRAME_CLASS;
