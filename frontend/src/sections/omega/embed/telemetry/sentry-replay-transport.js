import { scrubEmbedSecrets } from "./guard";
import { sentryScrubRecordingEvent } from "./sdk-guards";

const isReplayItem = ([header]) =>
  header.type === "replay_recording" || header.type === "replay_event";

// Normalize through the wire representation before scrubbing: URL/class objects
// and toJSON methods must not inject uninspected strings after the guard runs.
// Opaque binary/attachment data is deliberately dropped, never forwarded raw.
const scrubJson = (payload) => {
  if (payload instanceof ArrayBuffer || ArrayBuffer.isView(payload)) {
    throw new Error("Opaque telemetry payload");
  }
  const serialized =
    typeof payload === "string" ? payload : JSON.stringify(payload);
  return scrubEmbedSecrets(JSON.parse(serialized));
};

const scrubHeader = (header) => {
  const clean = scrubJson(header);
  // Keep identity for ordinary flat, unchanged envelope headers. Never retain
  // objects with accessors/toJSON that could change when serialized again.
  const descriptors = Object.getOwnPropertyDescriptors(header);
  const flatData = Object.values(descriptors).every(
    (entry) =>
      "value" in entry &&
      (entry.value === null ||
        ["string", "number", "boolean"].includes(typeof entry.value)),
  );
  return Object.getPrototypeOf(header) === Object.prototype &&
    flatData &&
    scrubEmbedSecrets(header) === header
    ? header
    : clean;
};

// Sentry 9.28.1's beforeAddRecordingEvent sees custom events only, NOT meta
// events. useCompression:false gives a JSON header, newline and rrweb array.
const scrubRecording = (payload) => {
  if (typeof payload !== "string") throw new Error("Unreadable replay format");
  const boundary = payload.indexOf("\n");
  if (boundary < 0) throw new Error("Missing replay header");
  const header = scrubJson(payload.slice(0, boundary));
  const events = JSON.parse(payload.slice(boundary + 1));
  if (!Array.isArray(events)) throw new Error("Invalid replay events");
  const clean = scrubEmbedSecrets(
    events.map(sentryScrubRecordingEvent).filter(Boolean),
  );
  return `${JSON.stringify(header)}\n${JSON.stringify(clean)}`;
};

const scrubItem = ([header, payload]) => {
  const cleanHeader = scrubHeader(header);
  const data =
    header.type === "replay_recording"
      ? scrubRecording(payload)
      : scrubJson(payload);
  const clean =
    header.type === "replay_recording" || typeof payload === "string"
      ? typeof data === "string" && header.type === "replay_recording"
        ? data
        : JSON.stringify(data)
      : data;
  const serialized = typeof clean === "string" ? clean : JSON.stringify(clean);
  return [
    header.length !== undefined || header.type === "replay_recording"
      ? { ...cleanHeader, length: new TextEncoder().encode(serialized).length }
      : cleanHeader,
    clean,
  ];
};

export function guardSentryTransport(transport) {
  return {
    send(envelope) {
      let headers;
      const safeItems = [];
      let rejectedReplay = false;
      try {
        headers = scrubHeader(envelope[0]);
        for (const item of envelope[1]) {
          try {
            safeItems.push(scrubItem(item));
          } catch {
            if (isReplayItem(item)) rejectedReplay = true;
          }
        }
      } catch {
        // No logging: even exceptions may contain the rejected credentials.
        return Promise.resolve({});
      }
      // Replay metadata and recording are one logical unit; discard both if
      // either is unreadable. Other items still have to pass the same scrubber.
      const items = rejectedReplay
        ? safeItems.filter((item) => !isReplayItem(item))
        : safeItems;
      return items.length
        ? transport.send([headers, items])
        : Promise.resolve({});
    },
    flush: (timeout) => transport.flush(timeout),
  };
}
