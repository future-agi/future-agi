import { describe, expect, it } from "vitest";
import {
  datasetOfReferrer,
  experimentMissing,
} from "../experimentNotFoundModel";

const O = "http://127.0.0.1:5280";
const DS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06";

describe("experimentNotFoundModel (RE-01)", () => {
  it("reads the dataset of a same-origin dataset page", () => {
    expect(
      datasetOfReferrer(`${O}/dashboard/develop/${DS}?tab=experiments`, O),
    ).toBe(DS);
    expect(
      datasetOfReferrer(
        `${O}/dashboard/develop/${DS}/compare/data?runs=a,b`,
        O,
      ),
    ).toBe(DS);
  });
  it("ignores other origins, other pages and experiment urls", () => {
    expect(
      datasetOfReferrer(`https://evil.example/dashboard/develop/${DS}`, O),
    ).toBeNull();
    expect(datasetOfReferrer(`${O}/dashboard/develop`, O)).toBeNull();
    expect(
      datasetOfReferrer(`${O}/dashboard/develop/experiment/x/data`, O),
    ).toBeNull();
    expect(datasetOfReferrer("", O)).toBeNull();
    expect(datasetOfReferrer("not a url", O)).toBeNull();
  });
  it("is missing only on a 404 error", () => {
    const statusOf = (e) => e?.statusCode ?? null;
    expect(
      experimentMissing(
        { isError: true, error: { statusCode: 404 } },
        statusOf,
      ),
    ).toBe(true);
    expect(
      experimentMissing(
        { isError: true, error: { statusCode: 500 } },
        statusOf,
      ),
    ).toBe(false);
    expect(experimentMissing({ isError: false }, statusOf)).toBe(false);
  });
});
