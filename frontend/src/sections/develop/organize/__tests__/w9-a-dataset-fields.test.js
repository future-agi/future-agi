// W9 A2 (W9-SCOPE §5): the list offers and names only the fields it
// answers; the quality fields wait for the run pair helper they share
// with Best.
import { describe, expect, it } from "vitest";
import {
  ANSWERED_DATASET_FIELDS,
  DATASET_FIELDS,
  unknownFieldSentence,
} from "../datasetFields";

describe("answered dataset fields", () => {
  it("leaves out exactly the quality fields", () => {
    const quality = DATASET_FIELDS.filter((f) => f.quality).map((f) => f.id);
    expect(quality.length).toBeGreaterThan(0);
    expect(ANSWERED_DATASET_FIELDS.map((f) => f.id)).toEqual(
      DATASET_FIELDS.filter((f) => !quality.includes(f.id)).map((f) => f.id),
    );
  });

  it("the unknown-field sentence lists what the list answers", () => {
    const s = unknownFieldSentence("lastpass");
    expect(s.startsWith("Unknown field “lastpass”. Fields: name, kind,")).toBe(
      true,
    );
    expect(s.endsWith(", metadata.<key>.")).toBe(true);
    ANSWERED_DATASET_FIELDS.forEach((f) => expect(s).toContain(f.word));
    DATASET_FIELDS.filter((f) => f.quality).forEach((f) =>
      expect(s).not.toContain(f.word),
    );
  });
});
