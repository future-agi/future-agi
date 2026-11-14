import { replayIntegration } from "@sentry-internal/replay";
import { scrubSamlRecordingEvent } from "./saml-telemetry-urls";

// Keep Replay behind its own import boundary, separate from startup monitoring.
export function createSentryReplay() {
  return replayIntegration({
    maskAllText: true,
    blockAllMedia: true,
    beforeAddRecordingEvent: scrubSamlRecordingEvent,
  });
}
