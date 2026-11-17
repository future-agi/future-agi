// v2.1 g8 (dyncol:rerun-scope): Save says what the re-run scope did.
import { describe, expect, it } from "vitest";
import { rerunResultToast } from "../rerunResult";

const res = (result) => ({ data: { result } });

describe("rerunResultToast", () => {
  it("names the column type and the rows re-run", () => {
    expect(
      rerunResultToast(
        "Classification",
        res({ message: "Re-running 12 rows", rows: 12 }),
      ),
    ).toEqual({
      text: "Classification column saved · Re-running 12 rows",
      variant: "success",
    });
  });
  it("an empty scope is info, not success", () => {
    expect(
      rerunResultToast(
        "API Calls",
        res({ message: "No rows match that scope", rows: 0 }),
      ).variant,
    ).toBe("info");
  });
  it("no message still names the column", () => {
    expect(rerunResultToast("Retrieval", undefined).text).toBe(
      "Retrieval column saved",
    );
  });
});
