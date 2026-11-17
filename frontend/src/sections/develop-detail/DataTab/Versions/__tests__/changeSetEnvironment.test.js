import { describe, expect, it } from "vitest";
import { applyOp, countOps, revertOp, toOperations } from "../changeSetStore";
import {
  describeChangeSet,
  environmentChangeText,
  pendingCountsText,
  pendingTotal,
  shortImage,
} from "../versionText";

const ENV = {
  image: "ghcr.io/acme/acme-support-sandbox:1.4",
  resources: { cpu: 2, memory_gb: 4 },
};
const change = (image) => ({
  op: "set_environment",
  image,
  before: ENV,
});

describe("set_environment in the change-set (R2 task suites)", () => {
  it("keeps one op with the first before; the original image drops it", () => {
    let ops = applyOp([], change("ghcr.io/acme/acme-support-sandbox:1.5"));
    ops = applyOp(ops, change("ghcr.io/acme/acme-support-sandbox:1.6"));
    expect(ops).toHaveLength(1);
    expect(ops[0].image).toBe("ghcr.io/acme/acme-support-sandbox:1.6");
    expect(ops[0].before).toEqual(ENV);
    expect(applyOp(ops, change(ENV.image))).toEqual([]);
  });

  it("keeps a change to another field with the same image", () => {
    const ops = applyOp([], {
      op: "set_environment",
      image: ENV.image,
      resources: { cpu: 4, memory_gb: 8 },
      before: ENV,
    });
    expect(ops).toHaveLength(1);
    expect(ops[0].resources).toEqual({ cpu: 4, memory_gb: 8 });
    expect(
      applyOp(ops, { op: "set_environment", ...ENV, before: ENV }),
    ).toEqual([]);
  });

  it("counts, describes and serialises the change", () => {
    const ops = applyOp([], change("ghcr.io/acme/acme-support-sandbox:1.5"));
    const counts = countOps(ops);
    expect(counts.environment).toEqual({
      from: ENV.image,
      to: "ghcr.io/acme/acme-support-sandbox:1.5",
    });
    expect(pendingCountsText(counts)).toBe(
      "Environment acme-support-sandbox:1.4 → 1.5",
    );
    expect(pendingTotal(counts)).toBe(1);
    expect(describeChangeSet(ops)).toBe(
      "Environment acme-support-sandbox:1.4 → 1.5",
    );
    const [wire] = toOperations(ops);
    expect(wire).toEqual({
      op: "set_environment",
      image: "ghcr.io/acme/acme-support-sandbox:1.5",
    });
    expect(revertOp(ops, ops[0].id)).toEqual([]);
  });

  it("counts read null without an environment change", () => {
    expect(countOps([]).environment).toBeNull();
    expect(pendingCountsText(countOps([]))).toBe("");
  });

  it("formats images", () => {
    expect(shortImage("ghcr.io/acme/acme-support-sandbox:1.4")).toBe(
      "acme-support-sandbox:1.4",
    );
    expect(shortImage("")).toBe("—");
    expect(environmentChangeText("a/b:1", "c/d:2")).toBe(
      "Environment b:1 → d:2",
    );
  });
});
