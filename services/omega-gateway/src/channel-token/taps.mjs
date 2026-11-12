// CX-WALL (X-1): how the gateway learns and follows channel tokens without holding a value anywhere
// but in memory, by digest (bindings.mjs). Both taps wrap the `fetch` EE is called with, for one
// request, and hand the browser EE's bytes unchanged.
//
//   learnMint     on the `channel-mint` route: a 201 JSON answer `{ channel: { token, conversationId,
//                 computerId, generation, expiresAt } }` is remembered with the mint path's bound values
//                 and the verified claims of the key that asked for it.
//   followChannel on a `channel` route: an event stream's `event: channel` renews the entry, an
//                 `event: end` with `computer_stopped` or `channel_token_invalid` forgets it, and a 401
//                 or a 409 answer forgets it too. Output events are passed on and never buffered.
const MAX_MINT_ANSWER_BYTES = 16_384;
// A line longer than this is an output event's data; only short `event:`/`data:` lines are read.
const MAX_READ_LINE = 4_096;
const FORGET_ON_END = new Set(["computer_stopped", "channel_token_invalid"]);

/** The fetch for a `channel-mint` request: EE's answer is passed on as is, and its token learned. */
export function learnMint(fetchImpl, { bindings, prefix, bound, claims }) {
  return async (url, init) => {
    const response = await fetchImpl(url, { ...init, redirect: "manual" });
    if (
      response.status !== 201 ||
      !String(response.headers.get("content-type") || "").includes(
        "application/json",
      )
    )
      return response;
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length <= MAX_MINT_ANSWER_BYTES) {
      try {
        const channel = JSON.parse(bytes.toString("utf8"))?.channel;
        // EE's answer must name the conversation the path named; otherwise nothing is learned.
        if (
          channel &&
          (bound.id === undefined || channel.conversationId === bound.id)
        ) {
          bindings.remember({ ...channel, prefix, bound, claims });
        }
      } catch {
        // Not a mint answer: passed on, nothing learned.
      }
    }
    return new Response(bytes, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}

/** Read the short `event:` and `data:` lines of an event stream, passing every byte on unchanged. */
function eventWatcher(onEvent) {
  const decoder = new TextDecoder();
  let partial = "";
  let skipping = false;
  let event = "message";
  let data = [];
  const line = (text) => {
    if (text === "") {
      if (data.length > 0) onEvent(event, data.join("\n"));
      event = "message";
      data = [];
      return;
    }
    if (text.startsWith("event:")) event = text.slice(6).trim();
    else if (
      text.startsWith("data:") &&
      (event === "channel" || event === "end")
    )
      data.push(text.slice(5).trimStart());
  };
  return new TransformStream({
    transform(chunk, controller) {
      controller.enqueue(chunk);
      const lines = (partial + decoder.decode(chunk, { stream: true })).split(
        /\r?\n/,
      );
      partial = lines.pop() ?? "";
      for (const text of lines) {
        if (skipping) {
          skipping = false;
          continue;
        }
        line(text);
      }
      if (partial.length > MAX_READ_LINE) {
        partial = "";
        skipping = true;
      }
    },
  });
}

/** The fetch for a `channel` request: renewals and refusals keep `bindings` in step with EE. */
export function followChannel(fetchImpl, { bindings, token }) {
  return async (url, init) => {
    const response = await fetchImpl(url, { ...init, redirect: "manual" });
    if (response.status === 401) bindings.forget(token);
    if (response.status === 409) {
      const bytes = Buffer.from(await response.arrayBuffer());
      try {
        const body = JSON.parse(bytes.toString("utf8"));
        if ((body?.error?.code ?? body?.code) === "computer_stopped")
          bindings.forget(token);
      } catch {}
      return new Response(bytes, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    }
    if (
      !response.body ||
      !String(response.headers.get("content-type") || "").includes(
        "text/event-stream",
      )
    )
      return response;
    const watched = response.body.pipeThrough(
      eventWatcher((event, data) => {
        try {
          const value = JSON.parse(data);
          if (event === "channel" && typeof value?.expiresAt === "string")
            bindings.renew(token, value.expiresAt);
          if (event === "end" && FORGET_ON_END.has(value?.code))
            bindings.forget(token);
        } catch {}
      }),
    );
    return new Response(watched, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}
