// R4 implementation: the connect components render their exact copy with no
// console errors (pill tones and owners, run strip). dataset integration FE-0: the
// trouble notices, the baton explainer and the live connection panel are
// removed with the live connection; the pill is no longer a link.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ConnectionPill from "../ConnectionPill";
import RunConnectionStrip from "../RunConnectionStrip";
import { MACHINES, DEMO_MACHINE } from "src/test-fixtures/connection-machines";

let errors;
beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  expect(errors.mock.calls.map((c) => String(c[0]).slice(0, 160))).toEqual([]);
  errors.mockRestore();
});

const wrap = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
const T = Date.now();
const iso = (ago) => new Date(T - ago).toISOString();
const m = (i, extra = {}) => ({
  ...MACHINES[i],
  last_seen_at: iso(2000),
  you: false,
  may_use: true,
  ...extra,
});

describe("ConnectionPill", () => {
  // dataset integration FE-0: no "update needed" tone (the SDK versions it compared do
  // not exist); ravi@thinkpad is offline.
  it("owner on every pill; your own reads you; locked, stale, offline", () => {
    wrap(
      <>
        <ConnectionPill machine={m(0)} />
        <ConnectionPill
          machine={{
            ...DEMO_MACHINE,
            last_seen_at: iso(1000),
            you: true,
            may_use: true,
          }}
        />
        <ConnectionPill machine={m(2, { last_seen_at: iso(2 * 3600_000) })} />
        <ConnectionPill machine={m(3, { may_use: false })} />
        <ConnectionPill machine={m(3, { may_use: true })} />
      </>,
    );
    const pills = [...document.querySelectorAll("[data-connection-pill]")];
    expect(pills.map((p) => p.getAttribute("data-pill-tone"))).toEqual([
      "online",
      "online",
      "stale",
      "locked",
      "offline",
    ]);
    expect(pills[0].getAttribute("aria-label")).toBe(
      "alice@mbp-14 · Alice Park · online",
    );
    expect(pills[1].getAttribute("aria-label")).toMatch(
      /^maya@macbook-air · you · online/,
    );
    expect(pills[2].textContent).toMatch(
      /ci-runner-7· Workspace runner· last seen 2h ago/,
    );
    expect(pills[3].getAttribute("aria-label")).toMatch(
      /^ravi@thinkpad · only Ravi Menon/,
    );
    expect(pills[4].textContent).toMatch(/offline/);
    expect(pills.map((p) => p.textContent).join(" ")).not.toMatch(/futureagi/);
  });
});

describe("RunConnectionStrip", () => {
  it("strip is null unless the run is local", () => {
    const { container } = wrap(
      <RunConnectionStrip
        run={{ id: "r", config: { execution: { mode: "sandbox" } } }}
      />,
    );
    expect(container.querySelector("[data-run-strip]")).toBeNull();
  });
});
