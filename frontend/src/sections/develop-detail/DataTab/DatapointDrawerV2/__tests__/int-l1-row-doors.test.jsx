// With the classic write API, the Row tab offers no
// door that does nothing. The "Other metadata" pencil (dev has no row
// metadata write) and the field ⋯ "Comment on this field" (dataset.collab is
// hidden in a launch build) do not render; the field editor's footer says
// the write happens on Apply.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MetadataSection from "../MetadataSection";

const here = dirname(fileURLToPath(import.meta.url));
const src = (f) => readFileSync(join(here, "..", f), "utf8");

const edit = {
  canEdit: true,
  editingId: null,
  start: vi.fn(),
  revert: vi.fn(),
  apply: vi.fn(),
  applyRef: { current: null },
};
const rowMeta = { value: { tier: "gold" }, edited: false };

describe("Other metadata pencil", () => {
  it("shows where the row's metadata can be written", () => {
    render(
      <MetadataSection
        fields={[]}
        rowMeta={rowMeta}
        mode="read"
        edit={edit}
        metaKey="__row_metadata__"
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Edit Other metadata" }),
    ).not.toBeNull();
  });

  it("does not render on dev's classic writes", () => {
    render(
      <MetadataSection
        fields={[]}
        rowMeta={rowMeta}
        mode="read"
        edit={edit}
        metaKey="__row_metadata__"
        canEditRowMeta={false}
      />,
    );
    expect(screen.queryByRole("button", { name: "Edit Other metadata" })).toBe(
      null,
    );
    // The section and its keys still read.
    expect(screen.getByText("Other metadata")).toBeTruthy();
  });

  it("RowFieldsTab turns it off whenever grid writes are classic", () => {
    expect(src("RowFieldsTab.jsx")).toMatch(
      /canEditRowMeta=\{!gridWritesClassic\(\)\}/,
    );
  });
});

describe("field ⋯ (comment, translate)", () => {
  it("uses separate comment and translate surfaces", () => {
    const s = src("RowFieldsTab.jsx");
    expect(s).toMatch(
      /canComment=\{!asOf && isSurfaceShown\("dataset\.collab\.comments"\)\}/,
    );
    expect(s).toMatch(
      /canTranslate=\{\s*isSurfaceShown\("dataset\.collab\.translate"\) &&\s*!\(asOf && rowNotInLatest\(row\)\)\s*\}/,
    );
    // FieldCollabMenu renders nothing with no target and no comment.
    expect(src("../../../common/Collab/FieldCollabMenu.jsx")).toMatch(
      /if \(!targets\.length && !canComment\) return null;/,
    );
  });
});

describe("field editor footer", () => {
  it("says Saved when you apply on classic writes, never the next version", () => {
    expect(src("FieldEditor.jsx")).toMatch(
      /gridWritesClassic\(\)\s*\?\s*"Saved when you apply\."\s*:\s*"Saved with the next version\."/,
    );
  });
});
