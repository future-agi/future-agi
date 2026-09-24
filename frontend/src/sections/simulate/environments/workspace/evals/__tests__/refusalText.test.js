import { describe, it, expect } from "vitest";
import { refusalText } from "../refusalText";

const GENERIC = "Couldn’t do the thing. Try again.";

// The error body attached to these endpoints declares `detail`, `message`,
// `error`, `result` and `details`, every one of them nullable. Reading only
// `detail` means a refusal whose text lands in one of the others is quoted
// verbatim on one screen and silently replaced by a generic sentence on the
// next.
describe("refusalText", () => {
  it("shows the server's sentence from whichever field carries it", () => {
    expect(refusalText({ detail: "no_misselling: needs conversation" }, GENERIC)).toBe(
      "no_misselling: needs conversation",
    );
    expect(
      refusalText({ message: "Environment is still building", statusCode: 409 }, GENERIC),
    ).toBe("Environment is still building");
    expect(refusalText({ error: "Run not found" }, GENERIC)).toBe("Run not found");
    expect(refusalText({ result: "Rate limit reached" }, GENERIC)).toBe("Rate limit reached");
  });

  it("prefers the more specific field when a body carries several", () => {
    const body = {
      detail: "the detail",
      message: "the message",
      error: "the error",
      result: "the result",
      statusCode: 400,
    };
    expect(refusalText(body, GENERIC)).toBe("the detail");
    expect(refusalText({ ...body, detail: undefined }, GENERIC)).toBe("the message");
    expect(refusalText({ ...body, detail: undefined, message: undefined }, GENERIC)).toBe(
      "the error",
    );
  });

  // The platform's global handler keys on `result`, but `result` is just as
  // often an object (field errors, an error code). Rendering that would put
  // "[object Object]" on screen, so it is used only when it is a string.
  it("ignores a `result` that is not a string", () => {
    expect(refusalText({ result: { error_code: "CAP_REACHED" } }, GENERIC)).toBe(GENERIC);
    expect(refusalText({ result: ["a", "b"] }, GENERIC)).toBe(GENERIC);
    expect(refusalText({ result: 42 }, GENERIC)).toBe(GENERIC);
    expect(refusalText({ result: { detail: "nested" } }, GENERIC)).not.toMatch(/nested/);
  });

  it("falls back to the caller's sentence for a body with nothing to say", () => {
    expect(refusalText({ statusCode: 500 }, GENERIC)).toBe(GENERIC);
    expect(refusalText(undefined, GENERIC)).toBe(GENERIC);
    expect(refusalText(null, GENERIC)).toBe(GENERIC);
    // A blank or whitespace-only field is nothing to say, not a blank line.
    expect(refusalText({ detail: "   " }, GENERIC)).toBe(GENERIC);
    expect(refusalText({ detail: "", message: "the message", statusCode: 400 }, GENERIC)).toBe(
      "the message",
    );
  });

  it("never renders a non-string field as text", () => {
    expect(
      refusalText({ detail: { a: 1 }, message: "the message", statusCode: 400 }, GENERIC),
    ).toBe("the message");
    expect(refusalText({ detail: { a: 1 } }, GENERIC)).toBe(GENERIC);
  });

  // A failure that never reached the server, and a request the client
  // refuses to send, both reject with a `message` our own plumbing wrote —
  // "Network Error", or an internal sentence about the request. Showing
  // either in place of the screen's written sentence swaps a plain
  // explanation for machinery. A status code is what says the body was
  // actually worded by the API.
  it("shows the caller's sentence rather than a message the API never sent", () => {
    expect(refusalText({ message: "Network Error" }, GENERIC)).toBe(GENERIC);
    expect(
      refusalText({ message: "API path is not in generated contract: /x/" }, GENERIC),
    ).toBe(GENERIC);
    expect(refusalText({ message: "Environment is still building", statusCode: 409 }, GENERIC)).toBe(
      "Environment is still building",
    );
    // The other fields only ever come from a body, so they are read whether
    // or not a status code came with them.
    expect(refusalText({ message: "Network Error", detail: "Run not found" }, GENERIC)).toBe(
      "Run not found",
    );
  });
});
