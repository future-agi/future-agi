// TH-150 unit tests for the pure clipboard helpers.
import { describe, it, expect, beforeEach } from "vitest";
import {
  normalizeMediaValue,
  mediaItemFromInsert,
  isMediaInsert,
} from "../Blots/mediaValue";
import { buildTextProjection, buildClipboardItems } from "./textProjection";
import * as store from "./internalClipboardStore";
import { RECORD_VERSION, TAB_ID } from "./constants";
import { embedIndicesInRange } from "./selectionHelpers";

describe("normalizeMediaValue", () => {
  it("accepts the flat create() shape and keeps callbacks aside", () => {
    const remove = () => {};
    const v = normalizeMediaValue(
      "image",
      { url: "u", name: "n", size: 1, id: "i", handleRemoveImage: remove },
      () => "gen",
    );
    expect(v).toEqual({
      id: "i",
      url: "u",
      name: "n",
      size: 1,
      mimeType: undefined,
      callbacks: { handleRemoveImage: remove },
    });
  });

  it("accepts the nested value() shape produced by history replay and paste", () => {
    expect(
      normalizeMediaValue("audio", {
        id: "a1",
        audioData: {
          url: "u",
          audio_name: "n",
          audio_size: 2,
          audio_type: "audio/wav",
        },
      }),
    ).toMatchObject({
      id: "a1",
      url: "u",
      name: "n",
      size: 2,
      mimeType: "audio/wav",
    });
    expect(
      normalizeMediaValue(
        "pdf",
        { pdfData: { url: "u", pdf_name: "d.pdf", pdf_size: 3 } },
        () => "fresh",
      ),
    ).toMatchObject({ id: "fresh", url: "u", name: "d.pdf", size: 3 });
  });

  it("mediaItemFromInsert drops URL-less or non-media inserts", () => {
    expect(mediaItemFromInsert({ insert: "text" })).toBeNull();
    expect(mediaItemFromInsert({ insert: { PdfBlot: true } })).toBeNull();
    expect(
      mediaItemFromInsert({
        insert: { ImageBlot: { imageData: { img_name: "x" } } },
      }),
    ).toBeNull();
    expect(
      mediaItemFromInsert({
        insert: { ImageBlot: { url: "u", name: "n", size: 1 } },
      }),
    ).toEqual({
      kind: "image",
      url: "u",
      name: "n",
      size: 1,
    });
    expect(isMediaInsert({ insert: { EditVariable: {} } })).toBe(false);
  });
});

describe("buildTextProjection / buildClipboardItems (AC-3.1, AC-10.1, AC-10.2)", () => {
  const ops = [
    { insert: "  a  {{x}" },
    { insert: { EditVariable: { fromBlock: false } } },
    { insert: "\n{% for i in items %}" },
    { insert: { EditVariable: { fromBlock: true } } },
    { insert: { PdfBlot: { url: "u", name: "d", size: 1 } } },
    { insert: "tail\n" },
  ];

  it("keeps whitespace, restores exactly one brace per inline EditVariable and nothing for fromBlock or media", () => {
    expect(buildTextProjection(ops)).toBe(
      "  a  {{x}}\n{% for i in items %}tail\n",
    );
  });

  it("produces ordered items with merged text runs", () => {
    expect(buildClipboardItems(ops)).toEqual([
      { kind: "text", text: "  a  {{x}" },
      { kind: "variableClose" },
      { kind: "text", text: "\n{% for i in items %}" },
      { kind: "pdf", url: "u", name: "d", size: 1 },
      { kind: "text", text: "tail\n" },
    ]);
  });
});

describe("internalClipboardStore.validateRecord (AC-15.1, AC-15.2)", () => {
  const live = { tabId: TAB_ID, userId: "u1", orgId: "o1", workspaceId: null };
  const good = () => ({
    version: RECORD_VERSION,
    handle: "h",
    createdAt: 1,
    text: "t",
    provenance: { ...live },
    items: [
      { kind: "text", text: "t" },
      { kind: "image", url: "https://x/y.png", name: "y.png", size: 1 },
    ],
  });

  beforeEach(() => store.clear());

  it("accepts a well-formed record from the same tab/user/org whose text matches the clipboard", () => {
    expect(store.validateRecord(good(), { clipboardText: "t", live })).toEqual({
      ok: true,
    });
  });

  it.each([
    ["unknown version", (r) => ({ ...r, version: 99 }), "invalid"],
    ["clipboard moved on", (r) => ({ ...r, text: "other" }), "other_context"],
    [
      "other tab",
      (r) => ({ ...r, provenance: { ...r.provenance, tabId: "t2" } }),
      "other_context",
    ],
    [
      "other user",
      (r) => ({ ...r, provenance: { ...r.provenance, userId: "u2" } }),
      "other_context",
    ],
    [
      "other org",
      (r) => ({ ...r, provenance: { ...r.provenance, orgId: "o2" } }),
      "other_context",
    ],
    [
      "function in item",
      (r) => ({ ...r, items: [{ kind: "pdf", url: "u", name: () => {} }] }),
      "invalid",
    ],
    [
      "extra key",
      (r) => ({ ...r, items: [{ kind: "pdf", url: "u", evil: 1 }] }),
      "invalid",
    ],
    [
      "empty url",
      (r) => ({ ...r, items: [{ kind: "pdf", url: "" }] }),
      "invalid",
    ],
    [
      "unknown kind",
      (r) => ({ ...r, items: [{ kind: "video", url: "u" }] }),
      "invalid",
    ],
  ])("rejects: %s", (_, mutate, reason) => {
    expect(
      store.validateRecord(mutate(good()), { clipboardText: "t", live }),
    ).toEqual({
      ok: false,
      reason,
    });
  });

  it("accepts CRLF clipboard text for a record written with LF (R1)", () => {
    const rec = { ...good(), text: "line1\nline2" };
    expect(
      store.validateRecord(rec, { clipboardText: "line1\r\nline2", live }),
    ).toEqual({ ok: true });
  });

  it("rejects everything when the live user is signed out", () => {
    expect(
      store.validateRecord(good(), {
        clipboardText: "t",
        live: { ...live, userId: null },
      }).ok,
    ).toBe(false);
  });

  it("get() only returns the record for its own handle", () => {
    store.put(good());
    expect(store.get("h")).not.toBeNull();
    expect(store.get("other")).toBeNull();
    expect(store.get("")).toBeNull();
  });
});

describe("embedIndicesInRange", () => {
  it("reports media embeds inside the range with Quill indices", () => {
    const ops = [
      { insert: "ab\n" },
      { insert: { ImageBlot: { id: "i" } } },
      { insert: { AudioBlot: { id: "a" } } },
      { insert: "c\n" },
    ];
    expect(embedIndicesInRange(ops, { index: 0, length: 4 })).toEqual([
      { index: 3, blotName: "ImageBlot", id: "i" },
    ]);
    expect(embedIndicesInRange(ops, { index: 4, length: 2 })).toEqual([
      { index: 4, blotName: "AudioBlot", id: "a" },
    ]);
    expect(embedIndicesInRange(ops, { index: 0, length: 3 })).toEqual([]);
  });
});
