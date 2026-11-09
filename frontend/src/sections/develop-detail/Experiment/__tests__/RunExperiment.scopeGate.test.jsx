import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import process from "node:process";
import { runInNewContext } from "node:vm";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  isSurfaceShown,
  setLaunchOverrideForTest,
} from "src/launch/launchSwitch";
import { scopeReceipt } from "../scopeReceipt";

// Execute the actual mutation callback without mounting the unrelated wizard
// steps. Only the callback's IO is replaced; the receipt and launch gates are real.
const source = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "RunExperiment.jsx"),
  "utf8",
);
let callbackCode;
beforeAll(() => {
  const callback = source.match(
    /create\.mutate\(wire\.body, \{\s*onSuccess:\s*(async \(res\) => \{[\s\S]*?\n {6}\}),\n {6}onError:/,
  );
  expect(callback, "create mutation success callback").not.toBeNull();
  // Compile outside jsdom: esbuild requires Node's Uint8Array/TextEncoder realm.
  callbackCode = execFileSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      `import { transformWithEsbuild } from "vite";
       import { readFileSync } from "node:fs";
       const { code } = await transformWithEsbuild(readFileSync(0, "utf8"), "create-success.jsx", { loader: "jsx", jsx: "transform" });
       process.stdout.write(code);`,
    ],
    { input: `(${callback[1]})`, encoding: "utf8" },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  setLaunchOverrideForTest(null);
});

const saved = {
  run_scope: {
    selected_count: 2,
    eligible_count: 3,
    source_count: 5,
    sample: { size: 2, seed: 17 },
  },
  limits: { messages: 1 },
  error_policy: "zero",
};

async function created({
  claimed = false,
  qualified = false,
  splitClaimed = false,
  splitQualified = false,
  persisted = saved,
  experimentsBasic = true,
  failed = false,
  prototype = false,
  external = false,
  experimentId = "exp-a",
} = {}) {
  // Only the existing source-test profile can lift this hold; app builds cannot.
  vi.stubEnv("VITE_DSX_CENSUS_B_TEST", qualified ? "true" : "false");
  setLaunchOverrideForTest({
    launch: !prototype,
    experimentsBasic,
    madeReal: [
      ...(claimed ? ["exp.new.scope"] : []),
      ...(splitClaimed ? ["exp.new.splitScope"] : []),
    ],
  });
  // Model a future F50-only hold removal at the callback's gate seam. Do not
  // use the global census opt-in: F05 must remain held even when it is claimed.
  const surfaceShown = (id) =>
    id === "exp.new.splitScope" && splitQualified
      ? splitClaimed
      : isSurfaceShown(id);
  const get = vi.fn();
  if (failed) get.mockRejectedValue(new Error("readback unavailable"));
  else get.mockResolvedValue({ data: { result: persisted } });
  const notify = vi.fn();
  const setReadingReceipt = vi.fn();
  const callback = runInNewContext(callbackCode, {
    React,
    Link: () => null,
    parseCreateResponse: () => ({
      experimentId,
      ordinal: null,
      examples: null,
      runs: [],
    }),
    scopeReceipt,
    isSurfaceShown: surfaceShown,
    isPrototypeMode: () => prototype,
    axios: { get },
    endpoints: {
      develop: {
        experiment: { getExperimentDetails: (id) => `/experiments/${id}` },
      },
    },
    setReadingReceipt,
    exampleCount: 99,
    nextOrdinal: null,
    data: { name: "Scope experiment" },
    runsTotal: 1,
    terminalRunsOf: () => [],
    flattenRuns: () => [],
    getValues: () => [],
    openAfterSubmit: { current: false },
    external,
    notify,
    MAX_TOAST_MS: 1000,
    invalidate: vi.fn(),
    refreshGrid: vi.fn(),
    setFormIsDirty: vi.fn(),
    onSubmitted: vi.fn(),
    navigate: vi.fn(),
  });
  await callback({ data: { result: {} } });
  expect(notify).toHaveBeenCalledTimes(1);
  return {
    get,
    setReadingReceipt,
    toast: render(notify.mock.calls[0][0]).container,
  };
}

describe("create toast scope admission", () => {
  it("does not fetch scope detail or show any policy line without a claim", async () => {
    const { get, setReadingReceipt, toast } = await created();
    expect(toast.textContent).toBe("“Scope experiment” started · 1 run · ");
    expect(toast.textContent).not.toMatch(
      /Server selected|Sample size|Messages|Errors|settings/,
    );
    expect(get).not.toHaveBeenCalled();
    expect(setReadingReceipt).not.toHaveBeenCalled();
  });

  it("does not fetch scope just because a held surface was claimed", async () => {
    const { get, setReadingReceipt, toast } = await created({ claimed: true });
    expect(get).not.toHaveBeenCalled();
    expect(setReadingReceipt).not.toHaveBeenCalled();
    expect(toast.textContent).not.toMatch(/Server selected|Sample size|Errors/);
  });

  it("reads persisted detail only with a claim and qualification opt-in", async () => {
    const { get, setReadingReceipt, toast } = await created({
      claimed: true,
      qualified: true,
    });
    expect(get).toHaveBeenCalledWith("/experiments/exp-a", { timeout: 8000 });
    expect(setReadingReceipt.mock.calls).toEqual([[true], [false]]);
    expect(toast.textContent).toContain("1 run × 2 rows");
    for (const line of scopeReceipt(saved).lines)
      expect(toast).toHaveTextContent(line);
    expect(toast).toHaveTextContent("Limits per row · 1 messages");
    expect(toast).toHaveTextContent("Errors count as 0 in scores");
    expect(toast.textContent).not.toContain("99");
  });

  it("reads the persisted F50 receipt while claimed F05 remains held", async () => {
    const { get, setReadingReceipt, toast } = await created({
      claimed: true,
      splitClaimed: true,
      splitQualified: true,
      // Even a stale detail response containing F05 fields cannot expose them.
      persisted: {
        ...saved,
        metadata: { private: "not a split receipt" },
        run_scope: { ...saved.run_scope, split_ids: ["split-a"] },
      },
    });
    expect(isSurfaceShown("exp.new.scope")).toBe(false);
    expect(get).toHaveBeenCalledWith("/experiments/exp-a", { timeout: 8000 });
    expect(setReadingReceipt.mock.calls).toEqual([[true], [false]]);
    expect(toast).toHaveTextContent("1 run × 2 rows");
    expect(toast).toHaveTextContent(
      "Server selected 2 of 3 eligible rows (5 source rows)",
    );
    expect(toast.textContent).not.toMatch(
      /Sample size|seed|Limits per row|messages|Errors|not a split receipt|99/,
    );
  });

  it("reports F50 readback failure independently of held F05", async () => {
    const { get, setReadingReceipt, toast } = await created({
      claimed: true,
      splitClaimed: true,
      splitQualified: true,
      failed: true,
    });
    expect(isSurfaceShown("exp.new.scope")).toBe(false);
    expect(get).toHaveBeenCalledTimes(1);
    expect(setReadingReceipt.mock.calls).toEqual([[true], [false]]);
    expect(toast).toHaveTextContent(
      "Experiment created; server settings could not be read back. Open the experiment to retry.",
    );
    expect(toast.textContent).not.toMatch(/99|Server selected|Errors count/);
  });

  it.each([{ splitQualified: true }])(
    "keeps unclaimed F50 inert (%j)",
    async (splitState) => {
      const { get, setReadingReceipt, toast } = await created({
        claimed: true,
        ...splitState,
      });
      expect(isSurfaceShown("exp.new.scope")).toBe(false);
      expect(get).not.toHaveBeenCalled();
      expect(setReadingReceipt).not.toHaveBeenCalled();
      expect(toast.textContent).not.toMatch(
        /Server selected|Sample size|Errors/,
      );
    },
  );

  it("reads the F50 receipt from its claim alone (all-on: no 749 hold)", async () => {
    const { get, setReadingReceipt, toast } = await created({
      claimed: true,
      splitClaimed: true,
      persisted: {
        ...saved,
        run_scope: { ...saved.run_scope, split_ids: ["split-a"] },
      },
    });
    expect(isSurfaceShown("exp.new.scope")).toBe(false);
    expect(isSurfaceShown("exp.new.splitScope")).toBe(true);
    expect(get).toHaveBeenCalledWith("/experiments/exp-a", { timeout: 8000 });
    expect(setReadingReceipt.mock.calls).toEqual([[true], [false]]);
    expect(toast).toHaveTextContent(
      "Server selected 2 of 3 eligible rows (5 source rows)",
    );
    expect(toast.textContent).not.toMatch(
      /Sample size|seed|Limits per row|messages|Errors|99/,
    );
  });

  it("does not expose readback errors when scope is hidden", async () => {
    const { get, toast } = await created({ failed: true });
    expect(get).not.toHaveBeenCalled();
    expect(toast.textContent).not.toMatch(
      /could not be read back|Errors|Server selected/,
    );
  });

  it("shows a bounded readback failure only when scope is admitted", async () => {
    const { setReadingReceipt, toast } = await created({
      claimed: true,
      qualified: true,
      failed: true,
    });
    expect(setReadingReceipt.mock.calls).toEqual([[true], [false]]);
    expect(toast).toHaveTextContent(
      "Experiment created; server settings could not be read back. Open the experiment to retry.",
    );
    expect(toast.textContent).not.toMatch(/99|Server selected|Errors count/);
  });

  it("keeps the parent experiment flag authoritative even with a scope claim", async () => {
    const { get, toast } = await created({
      claimed: true,
      qualified: true,
      experimentsBasic: false,
    });
    expect(get).not.toHaveBeenCalled();
    expect(toast.textContent).not.toMatch(/Server selected|Sample size|Errors/);
  });

  it("does not invent a row count when an external response omits the experiment id", async () => {
    const { get, toast } = await created({
      external: true,
      experimentId: null,
    });
    expect(get).not.toHaveBeenCalled();
    expect(toast.textContent).toBe(
      "“Scope experiment” created · waiting for results",
    );
  });

  it("does not add server readback to prototype creation", async () => {
    const { get, toast } = await created({ prototype: true });
    expect(get).not.toHaveBeenCalled();
    expect(toast).toHaveTextContent("1 run × 99 rows");
    expect(toast.textContent).not.toMatch(/Server selected|Errors/);
  });
});
