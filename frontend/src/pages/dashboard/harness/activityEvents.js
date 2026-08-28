const progressEventType = "harness.stage.progress";

const progressIdentity = (event) => {
  const payload = event?.payload || {};
  return JSON.stringify([
    event?.type,
    payload.stage || event?.stage || "",
    payload.activity || "",
    payload.detail || "",
    payload.message || "",
  ]);
};

export function compactActivityEvents(events = []) {
  const seen = new Set();
  const compacted = [];

  events.forEach((event) => {
    const eventKey = event.event_id || JSON.stringify(event);
    if (seen.has(eventKey)) return;
    seen.add(eventKey);

    const previous = compacted.at(-1);
    if (
      event.type === progressEventType &&
      previous?.type === progressEventType &&
      progressIdentity(previous) === progressIdentity(event)
    ) {
      compacted[compacted.length - 1] = event;
      return;
    }
    compacted.push(event);
  });

  return compacted;
}
