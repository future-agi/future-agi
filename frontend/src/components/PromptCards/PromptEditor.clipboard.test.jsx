// TH-150: rich (attachment-reference) copy/cut/paste between editors, bounded to
// the same tab, signed-in user and organization. AC ids from PRD r1.2.
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import PropTypes from "prop-types";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import PromptEditor from "./PromptEditor";
import { getBlocks } from "./common";
import * as store from "./clipboard/internalClipboardStore";
import { INTERNAL_MIME, TAB_ID } from "./clipboard/constants";
import {
  installRangePolyfill,
  clipboardStub,
  fireClipboard,
  flush,
  embedNodes,
} from "./test-fixtures/editorHarness";
import {
  DOC_TEXT_PDF_TEXT,
  DOC_IMG_AUDIO,
  PDF_1P,
  PNG_1x1,
  WAV_1S,
  textBlock,
  pdfBlockEmitted,
} from "./test-fixtures/media";

vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: () => <div data-testid="custom-audio-player" />,
}));

const enqueueSnackbar = vi.fn();
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar }),
}));

const authValue = (userId, orgId) => ({
  authenticated: true,
  user: { id: userId, organization: { id: orgId } },
});

function Harness({ auth, prompts, refs, props = {} }) {
  return (
    <AuthContext.Provider value={auth}>
      {prompts.map((prompt, i) => (
        <PromptEditor
          key={i}
          ref={refs[i]}
          prompt={prompt}
          onPromptChange={() => {}}
          appliedVariableData={{}}
          openVariableEditor={() => {}}
          setSelectedImage={() => {}}
          placeholder="Type here"
          allowedMediaTypes={["image", "audio", "pdf"]}
          {...(props[i] || {})}
        />
      ))}
    </AuthContext.Provider>
  );
}

Harness.propTypes = {
  auth: PropTypes.object.isRequired,
  prompts: PropTypes.array.isRequired,
  refs: PropTypes.array.isRequired,
  props: PropTypes.object,
};

function mountPair(
  prompts,
  { userId = "user-1", orgId = "org-O1", props } = {},
) {
  installRangePolyfill();
  const refs = prompts.map(() => React.createRef());
  const auth = authValue(userId, orgId);
  const utils = render(
    <Harness auth={auth} prompts={prompts} refs={refs} props={props} />,
  );
  const quills = refs.map((r) => r.current);
  quills.forEach((q) => q.history.cutoff());
  const rerenderAuth = (nextUserId, nextOrgId) =>
    utils.rerender(
      <Harness
        auth={authValue(nextUserId, nextOrgId)}
        prompts={prompts}
        refs={refs}
        props={props}
      />,
    );
  return { quills, rerenderAuth, ...utils };
}

function copyAll(quill) {
  quill.setSelection(0, quill.getLength() - 1, "silent");
  const cb = clipboardStub();
  fireClipboard(quill.root, "copy", cb);
  return cb;
}

async function pasteInto(quill, cb, index = 0) {
  quill.setSelection(index, 0, "silent");
  fireClipboard(quill.root, "paste", cb);
  await flush();
}

beforeEach(() => {
  store.clear();
  enqueueSnackbar.mockClear();
});

describe("TH-150 rich transfer inside the same tab/user/organization (REQ-4, REQ-5, REQ-13)", () => {
  it("AC-4.1: copy text/PDF/text from A into empty B keeps order, metadata, fresh ids and a working control; A unchanged", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    const cb = copyAll(a);
    expect(cb.data["text/plain"]).toBe("hello\nworld");
    expect(cb.data[INTERNAL_MIME]).toMatch(/\S/);
    expect(JSON.stringify(cb.data)).not.toContain(PDF_1P.url);
    const aBlocksBefore = getBlocks(a);

    await pasteInto(b, cb);
    expect(getBlocks(b)).toEqual([
      { type: "text", text: "hello\n" },
      pdfBlockEmitted,
      { type: "text", text: "world\n" },
    ]);
    expect(getBlocks(a)).toEqual(aBlocksBefore);
    const idA = embedNodes(a)[0].getAttribute("id");
    const idB = embedNodes(b)[0].getAttribute("id");
    expect(idB).not.toBe(idA);
    // AC-13.1: caret sits after the inserted range
    expect(b.getSelection()).toEqual({ index: b.getLength() - 1, length: 0 });
    // the pasted card's delete control is bound to B
    embedNodes(b)[0].querySelector("button").click();
    await flush();
    expect(embedNodes(b)).toHaveLength(0);
    expect(embedNodes(a)).toHaveLength(1);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("AC-5.2: a reference paste makes no upload or storage request", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(""));
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    await pasteInto(b, copyAll(a));
    expect(embedNodes(b)).toHaveLength(1);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("AC-8.2: undo of a paste restores B's prior content; redo re-inserts it with working controls", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("keep")]]);
    await pasteInto(b, copyAll(a), 4);
    expect(embedNodes(b)).toHaveLength(1);
    b.history.undo();
    await flush();
    expect(getBlocks(b)).toEqual([{ type: "text", text: "keep\n" }]);
    b.history.redo();
    await flush();
    expect(embedNodes(b)).toHaveLength(1);
    expect(JSON.parse(embedNodes(b)[0].getAttribute("data-pdf-data")).url).toBe(
      PDF_1P.url,
    );
    embedNodes(b)[0].querySelector("button").click();
    await flush();
    expect(embedNodes(b)).toHaveLength(0);
  });

  it("AC-12.1: image and audio paste in order; a destination that only accepts images drops the audio and reports it", async () => {
    const {
      quills: [a, b, c],
    } = mountPair([DOC_IMG_AUDIO, [textBlock("")], [textBlock("")]], {
      props: { 2: { allowedMediaTypes: ["image"] } },
    });
    const cb = copyAll(a);
    await pasteInto(b, cb);
    const kinds = getBlocks(b).map((blk) => blk.type);
    expect(kinds).toEqual(["text", "image_url", "audio_url", "text"]);
    expect(getBlocks(b)[2].audio_url).toEqual({
      url: WAV_1S.url,
      audio_name: WAV_1S.name,
      audio_size: WAV_1S.size,
      audio_type: WAV_1S.mimeType,
    });
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    await pasteInto(c, cb);
    expect(getBlocks(c).map((blk) => blk.type)).toEqual([
      "text",
      "image_url",
      "text",
    ]);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).toMatch(/not pasted/);
  });

  it("AC-16.1 (cut): cut captures then deletes in one step; a cut without clipboard access deletes nothing", () => {
    const {
      quills: [a],
    } = mountPair([DOC_TEXT_PDF_TEXT]);
    a.setSelection(0, a.getLength() - 1, "silent");
    const before = a.getContents().ops;
    // clipboardData missing entirely
    fireClipboard(a.root, "cut", undefined);
    expect(a.getContents().ops).toEqual(before);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    // setData throwing (denied) also leaves the source intact
    const denied = {
      setData: () => {
        throw new Error("denied");
      },
      getData: () => "",
    };
    fireClipboard(a.root, "cut", denied);
    expect(a.getContents().ops).toEqual(before);
    // a real cut removes exactly the selection and keeps the caret at its start
    const cb = clipboardStub();
    fireClipboard(a.root, "cut", cb);
    expect(cb.data["text/plain"]).toBe("hello\nworld");
    expect(a.getContents().ops).toEqual([{ insert: "\n" }]);
    expect(a.getSelection()).toEqual({ index: 0, length: 0 });
    // one undo brings everything back
    a.history.undo();
    expect(getBlocks(a)).toEqual([
      { type: "text", text: "hello\n" },
      pdfBlockEmitted,
      { type: "text", text: "world\n" },
    ]);
  });
});

describe("TH-150 provenance boundary (REQ-15, REQ-16)", () => {
  it("AC-15.1: a record from another organization pastes as text only and reports the omission", async () => {
    const {
      quills: [a, b],
      rerenderAuth,
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    const cb = copyAll(a);
    // the user switches organization before pasting
    rerenderAuth("user-1", "org-O2");
    await flush();
    await pasteInto(b, cb);
    expect(embedNodes(b)).toHaveLength(0);
    expect(getBlocks(b)).toEqual([{ type: "text", text: "hello\nworld\n" }]);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    // ...and the switch itself cleared the in-memory record
    expect(store.peek()).toBeNull();
  });

  it("AC-15.1: logout invalidates the record; a logged-out destination never gets attachments", async () => {
    const {
      quills: [a, b],
      rerenderAuth,
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    const cb = copyAll(a);
    rerenderAuth(null, null);
    await flush();
    await pasteInto(b, cb);
    expect(embedNodes(b)).toHaveLength(0);
    expect(getBlocks(b)[0].text).toBe("hello\nworld\n");
  });

  it("AC-15.1: a handle from another tab or after reload (no record) falls back to text with a notice", async () => {
    const {
      quills: [b],
    } = mountPair([[textBlock("")]]);
    const cb = clipboardStub({
      "text/plain": "from elsewhere",
      [INTERNAL_MIME]: "stale-handle",
    });
    await pasteInto(b, cb);
    expect(embedNodes(b)).toHaveLength(0);
    expect(getBlocks(b)).toEqual([{ type: "text", text: "from elsewhere\n" }]);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
  });

  it("AC-15.1: clipboard text that moved on since the copy is pasted as that text, not the stale attachments", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    const cb = copyAll(a);
    cb.setData("text/plain", "something else");
    await pasteInto(b, cb);
    expect(embedNodes(b)).toHaveLength(0);
    expect(getBlocks(b)).toEqual([{ type: "text", text: "something else\n" }]);
  });

  it("AC-15.2: a forged or tampered record never creates an embed and never reads clipboard-supplied provenance", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]]);
    const cb = copyAll(a);
    const record = store.peek();
    // tamper: inject a function and a foreign key into an item
    store.put({
      ...record,
      items: record.items.map((i) =>
        i.kind === "pdf" ? { ...i, onLoad: () => {}, extra: "x" } : i,
      ),
    });
    await pasteInto(b, cb);
    expect(embedNodes(b)).toHaveLength(0);
    // forged provenance in the record is compared against live app state, so
    // a record claiming another tab/org is rejected even with a matching handle
    const cb2 = copyAll(a);
    const rec2 = store.peek();
    store.put({
      ...rec2,
      provenance: { ...rec2.provenance, tabId: `${TAB_ID}-forged` },
    });
    await pasteInto(b, cb2, b.getLength() - 1);
    expect(embedNodes(b)).toHaveLength(0);
  });

  it("text-only copies carry no internal handle, so they paste anywhere as plain text without a notice", async () => {
    const {
      quills: [a, b],
    } = mountPair([[textBlock("just text")], [textBlock("")]]);
    const cb = copyAll(a);
    expect(cb.data[INTERNAL_MIME]).toBeUndefined();
    expect(store.peek()).toBeNull();
    await pasteInto(b, cb);
    expect(getBlocks(b)).toEqual([{ type: "text", text: "just text\n" }]);
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  it("AC-12.1 / V2: a mount that does not opt in (no allowedMediaTypes, like PromptImageInput/PromptTTSInput) never receives an attachment by paste", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]], {
      props: { 1: { allowedMediaTypes: undefined } },
    });
    await pasteInto(b, copyAll(a));
    expect(embedNodes(b)).toHaveLength(0);
    expect(getBlocks(b)).toEqual([{ type: "text", text: "hello\nworld\n" }]);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).toMatch(/not pasted/);
  });

  it("AC-12.1: PromptCard wiring — allowAttachment=false maps to an empty allow-list", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("")]], {
      props: { 1: { allowedMediaTypes: [] } },
    });
    await pasteInto(b, copyAll(a));
    expect(embedNodes(b)).toHaveLength(0);
  });

  it("R7: a paste with nothing this editor can insert (files only) leaves the document and selection untouched", async () => {
    const {
      quills: [b],
    } = mountPair([[textBlock("keep me")]]);
    b.setSelection(0, 4, "silent");
    const before = b.getContents().ops;
    const filesOnly = {
      getData: () => "",
      setData: () => {},
      files: [{ name: "shot.png" }],
      types: ["Files"],
    };
    fireClipboard(b.root, "paste", filesOnly);
    await flush();
    expect(b.getContents().ops).toEqual(before);
    expect(b.getSelection()).toEqual({ index: 0, length: 4 });
  });

  it("AC-16.1 (copy): a clipboard write that throws during copy reports the failure and changes nothing", () => {
    const {
      quills: [a],
    } = mountPair([DOC_TEXT_PDF_TEXT]);
    a.setSelection(0, a.getLength() - 1, "silent");
    const before = a.getContents().ops;
    const denied = {
      setData: () => {
        throw new Error("denied");
      },
      getData: () => "",
    };
    fireClipboard(a.root, "copy", denied);
    expect(a.getContents().ops).toEqual(before);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    // partial write: text/plain succeeds, the internal handle write throws
    const partial = clipboardStub();
    partial.setData = (type, value) => {
      if (type === INTERNAL_MIME) throw new Error("denied");
      partial.data[type] = value;
    };
    fireClipboard(a.root, "cut", partial);
    expect(a.getContents().ops).toEqual(before); // cut did not delete
    expect(enqueueSnackbar).toHaveBeenCalledTimes(2);
  });

  it("AC-10.1: variables on both sides of an attachment survive a cut/paste round trip", async () => {
    const {
      quills: [a, b],
    } = mountPair([
      [
        textBlock("{{first}} x"),
        {
          type: "pdf_url",
          pdf_url: {
            url: PDF_1P.url,
            file_name: PDF_1P.name,
            pdf_size: PDF_1P.size,
          },
        },
        textBlock("{{second}}"),
      ],
      [textBlock("")],
    ]);
    a.setSelection(0, a.getLength() - 1, "silent");
    const cb = clipboardStub();
    fireClipboard(a.root, "cut", cb);
    expect(cb.data["text/plain"]).toBe("{{first}} x\n{{second}}");
    expect(a.getContents().ops).toEqual([{ insert: "\n" }]);
    await pasteInto(b, cb);
    expect(getBlocks(b)).toEqual([
      { type: "text", text: "{{first}} x\n" },
      pdfBlockEmitted,
      { type: "text", text: "{{second}}\n" },
    ]);
    // the pasted variables are highlighted again (EditVariable chips)
    const chips = b
      .getContents()
      .ops.filter((op) => op.insert && op.insert.EditVariable);
    expect(chips).toHaveLength(2);
    // AC-13.1: the destination keeps focus
    expect(b.hasFocus()).toBe(true);
  });

  it("AC-11.1 (paste): a read-only destination is never mutated by a rich paste", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_TEXT_PDF_TEXT, [textBlock("ro")]], {
      props: { 1: { disabled: true } },
    });
    const cb = copyAll(a);
    const before = b.getContents().ops;
    await pasteInto(b, cb);
    expect(b.getContents().ops).toEqual(before);
  });
});

describe("TH-150 image metadata round trip (REQ-9)", () => {
  it("AC-9.1: pasted image keeps url/img_name/img_size through getBlocks", async () => {
    const {
      quills: [a, b],
    } = mountPair([DOC_IMG_AUDIO, [textBlock("")]]);
    await pasteInto(b, copyAll(a));
    const img = getBlocks(b).find((blk) => blk.type === "image_url");
    expect(img.image_url).toEqual({
      url: PNG_1x1.url,
      img_name: PNG_1x1.name,
      img_size: PNG_1x1.size,
    });
  });
});
