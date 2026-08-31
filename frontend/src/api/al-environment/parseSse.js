/**
 * Server-sent events arrive over POST here, so EventSource cannot be used — it is GET only.
 * That leaves fetch + ReadableStream, and parsing the wire format by hand.
 *
 * The parser is deliberately separate from the fetching so it can be tested against the
 * awkward cases without a network: an event split across two chunks, several events in one
 * chunk, keep-alive blanks, and a payload that is not valid JSON.
 */
export const createSseParser = (onEvent) => {
  let buffer = "";

  /** One "data: {...}" line becomes one event. Anything unparseable is dropped, not thrown. */
  const emit = (block) => {
    const payload = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .join("");

    if (!payload) return;

    try {
      onEvent(JSON.parse(payload));
    } catch {
      // A malformed frame must not take the rest of the stream with it: the harness is still
      // working, and the next frame is very likely fine.
    }
  };

  return {
    push(chunk) {
      buffer += chunk;
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        emit(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    },
    /** A stream can end without its final blank line; that last frame still counts. */
    end() {
      if (buffer.trim()) emit(buffer);
      buffer = "";
    },
  };
};
