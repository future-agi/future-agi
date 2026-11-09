// L7c g5 (runtray:pill, runtray:dock, runtray:finish-toast, sdk:waiting-copy):
// the tray, its docked chip and its tooltip say the same thing with the one
// waiting phrase; the finish toast names errored rows from the ledger.
import { describe, expect, it } from "vitest";
import { finishToastText, trayLabel } from "../runTrayStore";
import { WAIT_FOR, waitNounOf, waitingOf } from "../waitingModel";
import { ledgerOf, waitingPhraseOf } from "src/sections/common/Runs/runLedger";

const upload = {
  run_id: "u",
  label: "#6A",
  target_type: "external",
  status: "WaitingForResults",
};
const running = { run_id: "r", label: "#7A", status: "Running" };
const isWaiting = (r) => r.status === "WaitingForResults";
const entry = (r) => ({ runId: r.run_id, label: r.label });

describe("tray words", () => {
  it("a run that only waits is never going, and never waits for your code", () => {
    const live = { u: upload };
    const long = trayLabel([entry(upload)], live, isWaiting, {
      waitingPhrase: waitingPhraseOf,
    });
    expect(long).toBe("1 run waiting for results · #6A");
    expect(
      trayLabel([entry(upload)], live, isWaiting, {
        short: true,
        waitingPhrase: waitingPhraseOf,
      }),
    ).toBe("1 run waiting · #6A");
    expect(long).not.toMatch(/going|your code/);
  });
  it("mixed and running-only trays", () => {
    const live = { u: upload, r: running };
    expect(
      trayLabel([entry(running), entry(upload)], live, isWaiting, {
        waitingPhrase: waitingPhraseOf,
      }),
    ).toBe("2 runs · 1 going · #6A waiting for results");
    expect(trayLabel([entry(running)], { r: running }, isWaiting)).toBe(
      "1 run going",
    );
  });
  it("the waiting banner, the stand-in tag and the noun agree", () => {
    expect(waitNounOf(upload)).toBe("results");
    expect(waitingOf(upload).title).toBe("#6A waits for results");
    expect(WAIT_FOR.upload.event).toBe("the upload sending results");
    expect(JSON.stringify(WAIT_FOR)).not.toMatch(/your code/);
  });
});

describe("finish toast", () => {
  it("names the errored rows from the run's ledger", () => {
    const ledger = ledgerOf({
      records: [
        {
          example_id: "a",
          visibility: "error",
          status: "error",
          error: "lookup_invoice timeout after 30s",
        },
        {
          example_id: "b",
          visibility: "completed",
          status: "ok",
          scores: { k: { value: 1 } },
        },
      ],
      primaryKey: "k",
    });
    const text = finishToastText({ label: "#4A", ledger }, "Completed");
    expect(text).toMatch(/^#4A finished · Completed · 1 row errored/);
    expect(finishToastText({ label: "#9A" }, "Completed")).toBe(
      "#9A finished · Completed",
    );
  });
  it("names errored tries inside rows whose other tries ran (TS #4A)", () => {
    const tryOf = (row, trial, error) => ({
      example_id: row,
      trial,
      visibility: error ? "error" : "completed",
      status: error ? "error" : "ok",
      ...(error ? { error } : { scores: { k: { value: 1 } } }),
    });
    const ledger = ledgerOf({
      records: [
        tryOf("a", 1, null),
        tryOf("a", 2, "RateLimitError: 429 Too Many Requests"),
        tryOf("b", 1, null),
        tryOf("b", 2, null),
      ],
      primaryKey: "k",
    });
    const text = finishToastText({ label: "#4A", ledger }, "Completed");
    expect(text).toMatch(/^#4A finished · Completed · 1 try errored \(/);
    expect(text).not.toMatch(/row errored/);
  });
});
