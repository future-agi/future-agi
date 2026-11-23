import { describe, expect, it } from "vitest";

import { stopLiveLineOf, stopNoteOf, stopTipOf } from "../runStop";

const at = "2026-11-23T11:42:00Z";
const vapi = (status, confirmed_by = null) => ({
  status,
  kind: "provider",
  provider: "Vapi",
  requested_at: at,
  confirmed_at: confirmed_by ? at : null,
  confirmed_by,
});

describe("runStop", () => {
  it("says the run waits for the provider while a stop is requested", () => {
    expect(stopLiveLineOf(vapi("requested"), { voice: true })).toBe(
      "Stop requested: waiting for Vapi to confirm the calls in progress ended. No new calls start.",
    );
    expect(stopNoteOf(vapi("requested")).title).toMatch(
      /^Stop requested at .+ · waiting for confirmation$/,
    );
  });

  it("names how a confirmed stop was confirmed on hover, and shows no note", () => {
    expect(stopTipOf(vapi("confirmed", "provider_call_ended"))).toMatch(
      /^Stop requested at .+ · Vapi confirmed every call ended at .+$/,
    );
    expect(
      stopTipOf({
        ...vapi("confirmed", "sandbox_absent"),
        kind: "sandbox",
        provider: null,
      }),
    ).toMatch(/· sandbox verified gone at /);
    expect(stopNoteOf(vapi("confirmed", "provider_call_ended"))).toBeNull();
  });

  it("warns that an unconfirmed provider call keeps charging", () => {
    const note = stopNoteOf(vapi("unconfirmed", "provider_call_open"));
    expect(note.severity).toBe("warning");
    expect(note.title).toMatch(/· Vapi didn't confirm$/);
    expect(note.body).toMatch(
      /^Vapi still listed a call from this run as open after 5 minutes\. An open call keeps running and Vapi charges for it/,
    );
    expect(stopNoteOf(vapi("unconfirmed", "provider_unchecked")).body).toMatch(
      /^We couldn't check with Vapi/,
    );
  });

  it("says an agent at its own URL can't be confirmed", () => {
    const note = stopNoteOf({
      status: "requested",
      kind: "endpoint",
      provider: null,
      requested_at: at,
    });
    expect(note.title).toMatch(/· can't be confirmed$/);
    expect(note.body).toMatch(/^This agent runs at its own URL/);
  });

  it("treats a cleanup that failed as an error and says the agent may still run", () => {
    const note = stopNoteOf({
      status: "cleanup_failed",
      kind: "sandbox",
      provider: null,
      requested_at: at,
      confirmed_by: "cleanup_failed",
    });
    expect(note.severity).toBe("error");
    expect(note.body).toBe(
      "The stop wasn't confirmed after 10 minutes, so the agent may still be running. Finished trials are kept.",
    );
  });

  it("says why the provider couldn't be checked, and links to the key when it was missing", () => {
    const note = stopNoteOf({
      ...vapi("unconfirmed", "provider_unchecked"),
      reason: "no_key",
    });
    expect(note.body).toMatch(
      /^We couldn't check with Vapi that every call of this run ended \(no Vapi API key\)\./,
    );
    expect(note.action).toEqual({
      label: "Add the key in Settings",
      tab: "settings",
    });
    expect(
      stopNoteOf({
        ...vapi("unconfirmed", "provider_unchecked"),
        reason: "no_calls",
      }).action,
    ).toBeNull();
  });

  it("words a LiveKit stop like a code agent's, confirmed by its room being gone", () => {
    const stop = {
      status: "confirmed",
      kind: "sandbox",
      provider: "LiveKit",
      requested_at: at,
      confirmed_at: at,
      confirmed_by: "provider_call_ended",
    };
    expect(stopTipOf(stop)).toMatch(/· LiveKit room verified gone at /);
    expect(stopLiveLineOf(stop)).toBe(
      "Stopped: cleanup finished. Finished trials are kept.",
    );
  });
});
