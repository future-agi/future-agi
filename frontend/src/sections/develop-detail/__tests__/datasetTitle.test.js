import { describe, expect, it } from "vitest";
import { DATASETS_TITLE, datasetTitle } from "../datasetTitle";

describe("dataset page titles", () => {
  it("names the dataset, the tab and the product", () => {
    expect(datasetTitle("Support agent - golden set", "data")).toBe(
      "Support agent - golden set · Data · Future AGI",
    );
    expect(datasetTitle("Refund policy QA", "experiments")).toBe(
      "Refund policy QA · Experiments · Future AGI",
    );
  });
  it("falls back while the name loads or the tab is unknown", () => {
    expect(datasetTitle(undefined, undefined)).toBe(
      "Dataset · Data · Future AGI",
    );
    expect(datasetTitle("  ", "bogus")).toBe("Dataset · Data · Future AGI");
  });
  it("marks compare mode", () => {
    expect(datasetTitle("RAG citations", "summary", true)).toBe(
      "Compare · RAG citations · Summary · Future AGI",
    );
  });
  it("titles the list", () => {
    expect(DATASETS_TITLE).toBe("Datasets · Future AGI");
  });
});
