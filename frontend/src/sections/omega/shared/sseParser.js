/** Incremental text/event-stream parser (WHATWG HTML "event stream interpretation"). */
export function createSseParser(onEvent) {
  let buffer = "";
  let data = [];
  let eventType = "";
  let lastEventId = null;
  const dispatch = () => {
    if (data.length === 0) {
      eventType = "";
      return;
    }
    const text = data.join("\n");
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      json = undefined;
    }
    onEvent({
      id: lastEventId,
      event: eventType || "message",
      data: text,
      json,
    });
    data = [];
    eventType = "";
  };
  const line = (raw) => {
    if (raw === "") return dispatch();
    if (raw.startsWith(":")) return undefined;
    const colon = raw.indexOf(":");
    const field = colon === -1 ? raw : raw.slice(0, colon);
    let value = colon === -1 ? "" : raw.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data.push(value);
    else if (field === "event") eventType = value;
    else if (field === "id" && !value.includes("\0")) lastEventId = value;
    return undefined;
  };
  return {
    push(chunk) {
      buffer += chunk;
      const parts = buffer.split(/\r\n|\r|\n/);
      buffer = parts.pop();
      parts.forEach(line);
    },
    get lastEventId() {
      return lastEventId;
    },
  };
}
