// R4 implementation: every shared stub accepts its frozen props, renders its
// sized `Not built yet · lane <id>` box (or the documented real fallback)
// and logs nothing (no PropTypes warnings).
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import MediaValue from "../Media/MediaValue";
import ModalityLens from "../Lenses/ModalityLens";
import LensLab from "../Lenses/LensLab";
import ConnectionPill from "../Connect/ConnectionPill";
import RunConnectionStrip from "../Connect/RunConnectionStrip";
import RunProvenance from "../Runs/RunProvenance";
import RerunWithChangesHost from "../RunSources/RerunWithChangesHost";
import RunOnRowsButton from "../RunSources/RunOnRowsButton";
import SourceLogo from "../RunSources/SourceLogo";
import { openRerunWithChanges, rerunRequestNow } from "../RunSources/rerunBus";
import PathsView from "src/sections/experiment-detail/paths/PathsView";
import { TS3_CONFIGS } from "src/test-fixtures/run-configs";
import { CALL_VALUE_EXAMPLE } from "src/test-fixtures/call-value";

// The rerun host opens only in the prototype build: on the real classic page
// the experiment owns its own append dialog (T-XP exp.rerun). Production
// caches the build mode at import time, so the case that needs the prototype
// build switches it here; every other case keeps the real-build default.
const buildMode = vi.hoisted(() => ({ prototype: false }));
vi.mock("src/utils/prototype", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, isPrototypeMode: () => buildMode.prototype };
});

let errors;
beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  buildMode.prototype = false;
  expect(errors.mock.calls.map((c) => String(c[0]).slice(0, 120))).toEqual([]);
  errors.mockRestore();
});

const wrap = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/dashboard/develop/r4-lab?logos=1"]}>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>,
  );
const stub = (lane, name) =>
  document.querySelector(
    `[data-r4-stub="${lane}"][data-r4-stub-name="${name}"]`,
  );

describe("R4 stubs (§I1)", () => {
  it("render sized boxes with their lane and name", () => {
    wrap(
      <>
        <ModalityLens
          kind="call"
          lanes={[
            {
              runId: "a",
              label: "#1A",
              color: "#f00",
              isBaseline: true,
              value: CALL_VALUE_EXAMPLE,
            },
          ]}
          rowId="r"
          keysEnabled={false}
        />
        <ConnectionPill machineId="cm_alice_mbp14" size="medium" />
        <RunConnectionStrip
          run={{ config: { execution: { mode: "local" } } }}
        />
        <PathsView datasetId="d" runIds={["a", "b"]} baselineId="a" />
      </>,
    );
    // R4 replaced ModalityLens (merged in R4 implementation): the real lens
    // loads lazily with the same frozen props and leaves no implementation box.
    expect(document.querySelector('[data-r4-stub="M"]')).toBeNull();
    // R4 replaced these stub bodies (merged in R4 implementation): the real
    // ConnectionPill / RunConnectionStrip render with the same frozen props
    // and leave no implementation box behind (dataset integration FE-0: the live connection
    // panel and the Runners page are removed).
    expect(document.querySelector('[data-r4-stub="C"]')).toBeNull();
    // R4 replaced the PathsView stub body (same frozen props): no implementation
    // box remains.
    expect(document.querySelector('[data-r4-stub="T"]')).toBeNull();
    expect(document.querySelector('[data-testid="paths-view"]')).toBeTruthy();
  });

  it("RunConnectionStrip is null for runs not on a connected machine", () => {
    const { container } = wrap(
      <RunConnectionStrip
        run={{ config: { execution: { mode: "sandbox" } } }}
      />,
    );
    expect(container.querySelector("[data-r4-stub]")).toBeNull();
  });

  it("MediaValue renders text unchanged, media as its label", () => {
    wrap(
      <>
        <MediaValue value="plain answer" size="cell" />
        <MediaValue value={CALL_VALUE_EXAMPLE} size="cell" />
        <MediaValue
          value='["/m/a.png","/m/b.png","/m/c.png"]'
          columnType="images"
          size="cell"
        />
      </>,
    );
    expect(screen.getByText("plain answer")).toBeTruthy();
    // The real body: a call in a cell is a CallChip, not a label.
    expect(document.querySelector("[data-call-chip]")).toBeTruthy();
    expect(document.querySelector('[data-image-strip="3"]')).toBeTruthy();
  });

  it("RunProvenance renders the real line, chips and card", () => {
    wrap(
      <>
        <RunProvenance run={{ config: TS3_CONFIGS.A }} variant="line" />
        <RunProvenance run={{ config: TS3_CONFIGS.E }} variant="chips" />
        <RunProvenance run={{ config: TS3_CONFIGS.E }} variant="card" />
      </>,
    );
    expect(
      screen.getByText(
        "LangGraph · gpt-4.1-mini +1 · 7 tools · MCP acme-support · alice@mbp-14 · 9f2c1a7",
      ),
    ).toBeTruthy();
    expect(
      document.querySelector('[data-source-logo="langgraph"]'),
    ).toBeTruthy();
    // R4 replaced the chips and card stubs (merged in R4 implementation).
    expect(document.querySelector('[data-r4-stub="X"]')).toBeNull();
  });

  it("RunOnRowsButton is a live button with its label (lane Rb body)", () => {
    wrap(
      <RunOnRowsButton
        label="Re-test these rows"
        variant="toastAction"
        datasetId="d"
        rowIds={["r1"]}
      />,
    );
    // Lane Rb replaced the disabled stub (merged in R4 implementation).
    const btn = screen.getByRole("button", { name: "Re-test these rows" });
    expect(btn.disabled).toBe(false);
    expect(document.querySelector('[data-r4-stub="Rb"]')).toBeNull();
  });

  it("RerunWithChangesHost opens on a rerun request", () => {
    buildMode.prototype = true;
    wrap(<RerunWithChangesHost />);
    expect(stub("Ra", "RerunWithChangesHost")).toBeNull();
    act(() =>
      openRerunWithChanges({
        runId: "a6000000-0000-4000-8000-00000000001b",
        scope: "failed",
      }),
    );
    expect(rerunRequestNow()).toMatchObject({
      runId: "a6000000-0000-4000-8000-00000000001b",
      scope: "failed",
      rowIds: [],
    });
    // Lane Ra replaced the stub body: the real host opens its dialog (here
    // with no dataset on the route it says the run isn't on this page).
    expect(screen.getByText("Rerun with changes…")).toBeTruthy();
  });

  it("SourceLogo renders the bundled brand mark, never a monogram, offline", () => {
    wrap(
      <>
        <SourceLogo entryId="claude_code" size={16} />
        <SourceLogo entryId="llamaindex" size={28} withName />
        <SourceLogo entryId="langgraph" size={16} tooltip />
      </>,
    );
    // jsdom has no network: every mark is bundled, so all three draw.
    ["claude_code", "llamaindex", "langgraph"].forEach((id) =>
      expect(
        document.querySelector(`[data-source-logo="${id}"] svg`),
        id,
      ).toBeTruthy(),
    );
    expect(screen.getByText("LlamaIndex")).toBeTruthy();
    expect(screen.queryByText("LI")).toBeNull();
    expect(screen.queryByText("LG")).toBeNull();
  });

  // dataset integration FE-0: the lab lists what the picker offers (3 entries; Traces
  // I already have waits for SRV-13).
  it("LensLab ?logos=1 lists every offered entry", () => {
    wrap(<LensLab />);
    expect(document.querySelectorAll("[data-logo-cell]")).toHaveLength(3);
  });
});
