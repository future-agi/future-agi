import { describe, expect, it, vi } from "vitest";

import { guardSentryTransport } from "../telemetry/sentry-replay-transport";

const URL =
  "/auth/jwt/invitation/accept/fake-user/fake-credential-for-unit-test?keep=1";
const CLEAN =
  "/auth/jwt/invitation/accept/fake-user/auth-secret-redacted?keep=1";
const recording = (events) =>
  `${JSON.stringify({ segment_id: 0 })}\n${JSON.stringify(events)}`;
const makeEnvelope = (payload) => [
  { event_id: "fake-event-id" },
  [
    [{ type: "replay_event" }, { urls: [URL] }],
    [{ type: "replay_recording", length: 1 }, payload],
  ],
];
const makeTransport = () => {
  const base = {
    send: vi.fn().mockResolvedValue({ statusCode: 200 }),
    flush: vi.fn().mockResolvedValue(true),
  };
  return { base, guarded: guardSentryTransport(base) };
};

describe("Sentry 9 replay transport boundary", () => {
  it("scrubs meta URLs even though the installed SDK only calls its recording hook for type 5", async () => {
    const { base, guarded } = makeTransport();
    const meta = { type: 4, data: { href: URL, width: 800 }, timestamp: 1 };
    const custom = {
      type: 5,
      data: { payload: { description: URL } },
      timestamp: 2,
    };
    const dom = {
      type: 2,
      data: { node: { textContent: "masked" } },
      timestamp: 3,
    };
    const input = makeEnvelope(recording([meta, custom, dom]));
    await guarded.send(input);
    expect(base.send).toHaveBeenCalledTimes(1);
    const sent = base.send.mock.calls[0][0];
    expect(sent[0]).toBe(input[0]);
    expect(sent[1][0][1].urls).toEqual([CLEAN]);
    const [header, body] = sent[1][1][1].split("\n");
    expect(JSON.parse(header)).toEqual({ segment_id: 0 });
    expect(JSON.parse(body)).toEqual([
      { ...meta, data: { ...meta.data, href: CLEAN } },
      { ...custom, data: { payload: { description: CLEAN } } },
      dom,
    ]);
    expect(sent[1][1][0].length).toBe(
      new TextEncoder().encode(sent[1][1][1]).length,
    );
    expect(input[1][1][1]).toContain("fake-credential-for-unit-test");
  });

  it.each([
    "not-json",
    '{"segment_id":0}\nnot-json',
    '{"segment_id":0}\n{}',
    new Uint8Array([1, 2, 3]),
  ])(
    "fails closed on malformed or compressed recordings without forwarding replay metadata",
    async (payload) => {
      const { base, guarded } = makeTransport();
      await guarded.send(makeEnvelope(payload));
      expect(base.send).not.toHaveBeenCalled();
    },
  );

  it("keeps non-replay telemetry and flush semantics, including when a replay is rejected", async () => {
    const { base, guarded } = makeTransport();
    const errorItem = [{ type: "event" }, { message: "ordinary error" }];
    const ordinary = [{}, [errorItem]];
    await guarded.send(ordinary);
    expect(base.send).toHaveBeenLastCalledWith(ordinary);
    const mixed = makeEnvelope(new Uint8Array([1, 2, 3]));
    mixed[1].push(errorItem);
    await guarded.send(mixed);
    expect(base.send).toHaveBeenLastCalledWith([mixed[0], [errorItem]]);
    await expect(guarded.flush(123)).resolves.toBe(true);
    expect(base.flush).toHaveBeenCalledWith(123);
  });

  it("also scrubs each auth query and encoded returnTo in actual serialized replay data", async () => {
    for (const param of [
      "sso_token",
      "access_token",
      "refresh_token",
      "onboarding_token",
      "onboarding_gcp_token",
      "access",
      "refresh",
    ]) {
      const { base, guarded } = makeTransport();
      const href = `/callback?${param}=fake-credential-for-unit-test&returnTo=${encodeURIComponent(URL)}`;
      await guarded.send(
        makeEnvelope(recording([{ type: 4, data: { href } }])),
      );
      const sent = base.send.mock.calls[0][0];
      expect(sent[1][1][1]).not.toContain("fake-credential-for-unit-test");
      expect(sent[1][1][1]).toContain(`${param}=auth-secret-redacted`);
      expect(sent[1][1][1]).toContain(
        "returnTo=%2Fauth%2Fjwt%2Finvitation%2Faccept%2Ffake-user%2Fauth-secret-redacted%3Fkeep%3D1",
      );
    }
  });
});
