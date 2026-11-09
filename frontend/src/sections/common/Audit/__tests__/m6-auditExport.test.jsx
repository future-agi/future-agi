// M6 (W4 §9, the audit Export CSV integration): the log's filtered entries go
// out through the one CSV writer (writers.js), every page of them.
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toCsv } from "src/sections/common/export/writers";
import { AUDIT_CSV_COLUMNS, auditCsvName, auditCsvRows } from "../auditModel";

const entry = (i) => ({
  id: `e${i}`,
  at: `2026-10-23T10:0${i}:00.000Z`,
  action: "dataset.access_changed",
  action_label: "changed access",
  actor: { id: "u1", name: "Maya Chen" },
  scope: { kind: "dataset", id: "d1", name: "Support agent - golden set" },
  target: { kind: "user", id: "u2", label: "Asha Rao" },
  summary: "Asha Rao: View → Edit",
  changes: { before: { level: "view" }, after: { level: "edit" } },
  diff: [],
});
const PAGE = {
  data: {
    pages: [
      { data: { total: 3, groups: [], actors: [], entries: [entry(1)] } },
    ],
  },
  isLoading: false,
  hasNextPage: false,
};
const fetchAll = vi.fn(() => Promise.resolve([entry(1), entry(2), entry(3)]));
vi.mock("src/api/develop/dataset-access", () => ({
  useAuditInfinite: () => PAGE,
  fetchAllAudit: (...args) => fetchAll(...args),
}));
const saved = [];
vi.mock("src/sections/common/export/datasetExport", () => ({
  saveFile: (f) => {
    saved.push(f);
    return f.data.length;
  },
}));
vi.mock("src/sections/common/ux/toast", () => ({ notify: vi.fn() }));

const { AuditLogPanel } = await import("../AuditDrawer");

describe("audit Export CSV", () => {
  it("rows and the file name", () => {
    const csv = toCsv(auditCsvRows([entry(1)]), AUDIT_CSV_COLUMNS);
    expect(csv.split("\r\n")[0]).toBe(
      "When,Person,Action,Where,What,Summary,Before,After,Note",
    );
    expect(csv).toContain(
      "Maya Chen,changed access,Support agent - golden set",
    );
    expect(csv).toContain('"{""level"":""view""}"');
    expect(auditCsvName("Support agent - golden set")).toBe(
      "audit-log-support-agent-golden-set.csv",
    );
    expect(auditCsvName()).toBe("audit-log.csv");
  });

  it("Export CSV saves every entry of the current filters", async () => {
    render(
      <AuditLogPanel scope="dataset:d1" title="Support agent - golden set" />,
    );
    fireEvent.click(screen.getByTestId("audit-export-csv"));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(fetchAll.mock.calls[0][0]).toMatchObject({ scope: "dataset:d1" });
    expect(saved[0]).toMatchObject({
      mime: "text/csv",
      name: "audit-log-support-agent-golden-set.csv",
    });
    expect(saved[0].data.trim().split("\r\n")).toHaveLength(4);
  });
});
