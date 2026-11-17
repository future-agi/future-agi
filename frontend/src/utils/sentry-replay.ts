import { replayIntegration } from "@sentry-internal/replay";
import {
  SENTRY_REPLAY_BLOCK_SELECTORS,
  sentryBeforeAddRecordingEvent,
} from "../sections/omega/embed/telemetry/sdk-guards";
import { scrubSamlRecordingEvent } from "./saml-telemetry-urls";

// Keep Replay behind its own import boundary, separate from startup monitoring.
// Mask text and media so no customer content (prompts, PII, uploads) streams to
// a third party. Omega frames are blocked, and nothing is recorded on the pages
// that hold them.
export function createSentryReplay() {
  return replayIntegration({
    // Meta events bypass beforeAddRecordingEvent in Sentry 9.28.1. Keep the
    // envelope readable for the fail-closed transport scrubber in main.jsx.
    useCompression: false,
    maskAllText: true,
    blockAllMedia: true,
    block: [...SENTRY_REPLAY_BLOCK_SELECTORS],
    // SAML sign-in URLs are projected first; the embed guard decides last.
    beforeAddRecordingEvent: (event) =>
      sentryBeforeAddRecordingEvent(scrubSamlRecordingEvent(event)),
  });
}
