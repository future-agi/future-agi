import { describe, expect, it } from "vitest";
import { SUPPORTED, SUPPORTED_LINE, readFile, sourceFromOrg } from "../orgData";
import { ORG_SOURCES, kbRefreshOf } from "../kbCatalog";
import {
  kbRefreshPlanOf,
  kbRefreshPatch,
  validateKbRefresh,
} from "../kbRefresh";
import {
  approveIdMappingPatch,
  personalDataOf,
  standInId,
} from "../dataMapping";
import { seedSourcesPatch } from "../orgSourceSeed";
import { sourceRevisionsLine } from "../sourceRevisions";

const env = { id: "env-refunds-desk", rules: [], tools: [] };

describe("formats", () => {
  it("reads every advertised type and the error lists the same ones", () => {
    SUPPORTED.forEach((e) => {
      expect(SUPPORTED_LINE).toContain(e.toUpperCase());
      expect(readFile({ name: `file.${e}` }).status).not.toBe("unsupported");
    });
    const bad = readFile({ name: "file.exe" });
    expect(bad.status).toBe("unsupported");
    expect(bad.statusNote).toContain("DOCX");
  });
});

describe("Knowledge base refresh", () => {
  const seeded = () => {
    const patch = seedSourcesPatch(env, {});
    return { ...patch };
  };
  it("seeds a source one revision behind and one revoked", () => {
    const es = seeded();
    const policy = es.sources.find((s) => s.name === "Refund policy 2026.pdf");
    expect(kbRefreshOf(policy)).toMatchObject({ from: 2, latest: 3 });
    expect(es.sources.find((s) => s.revoked)).toBeTruthy();
    expect(kbRefreshOf(es.sources.find((s) => s.revoked))).toBeNull();
  });
  it("fails validation on rows naming missing customers, then passes once rejected", () => {
    const es = seeded();
    const orders = es.sources.find((s) => s.name === "orders-q3-sample.csv");
    const plan = kbRefreshPlanOf(es, orders);
    const all = plan.changes.map((c) => c.id);
    const bad = validateKbRefresh(es, plan, all);
    expect(bad.ok).toBe(false);
    expect(bad.failures[0].rows.map((r) => r.row)).toEqual([511, 538]);
    expect(validateKbRefresh(es, plan, ["c2", "c3"]).ok).toBe(true);
  });
  it("moves the source to the new revision and names the trial's revisions", () => {
    const es = {
      ...seeded(),
      envVersions: ["v1", "v2", "v3"].map((label) => ({ id: label, label })),
    };
    const policy = es.sources.find((s) => s.name === "Refund policy 2026.pdf");
    const plan = kbRefreshPlanOf(es, policy);
    const saved = kbRefreshPatch(env, es, policy, plan, ["c1", "c2", "c3"]);
    const next = { ...es, ...saved };
    const moved = next.sources.find((s) => s.id === policy.id);
    expect(moved.kb.revision).toBe(3);
    expect(moved.parsed.passages.find((p) => p.id === "p1").text).toContain(
      "14 days",
    );
    expect(sourceRevisionsLine(next, "v3")).toContain(
      "Refund policy 2026.pdf rev 2",
    );
    expect(sourceRevisionsLine(next, saved.activeEnvVersion)).toContain(
      "Refund policy 2026.pdf rev 3",
    );
  });
});

describe("ID mapping (DA-1)", () => {
  it("maps buyer_id to customer_id and replaces C17 with the same stand-in in both files", () => {
    const es = seedSourcesPatch(env, {});
    const orders = es.sources.find((s) => s.name === "orders-q3-sample.csv");
    const out = approveIdMappingPatch(es, orders, "buyer_id", {
      replace: true,
    });
    const o = out.sources.find((s) => s.id === orders.id);
    const c = out.sources.find((s) => s.name === "customers.csv");
    expect(o.parsed.columns.find((x) => x.name === "buyer_id").ref).toBe(
      "customers.customer_id",
    );
    expect(o.privacy.buyer_id).toBe("replace");
    expect(c.privacy.customer_id).toBe("replace");
    expect(standInId("C17")).toBe("CUST-1017");
    expect(o.parsed.rejected).toHaveLength(2);
  });
});

describe("personal data", () => {
  it("is undecided until the person chooses", () => {
    const s = readFile({ name: "customers.csv" });
    expect(personalDataOf(s)).toMatchObject({ has: true, choice: null });
  });
  it("copies a Knowledge base revision, pinned", () => {
    const s = sourceFromOrg({ ...ORG_SOURCES[0], revision: 2 });
    expect(s.kb.revision).toBe(2);
    expect(s.parsed.passages[0].text).toContain("30 days");
  });
});
