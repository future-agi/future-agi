// W9 integration review fixes: M1 (the renderer sits in a wrapper frame whose
// frame-src 'none' refuses its navigation; only the renderer's window is
// heard, and from the wrapper only its report of what it refused), M2 (a right-side field keeps its editor while the sides stack),
// M3 (a copy of someone else's renderer keeps its author and asks first).
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import {
  MSG,
  RENDERER_CSP,
  RENDERER_SANDBOX,
  WRAPPER_CSP,
  WRAPPER_SCRIPT,
  buildFrameDoc,
} from "../sandboxDoc";
import { acceptWrapper, rendererWindow } from "../rendererBridge";
import SandboxedRenderer, { RendererSlot } from "../SandboxedRenderer";
import RowLayoutView from "../RowLayoutView";
import { defaultLayout, layoutColumns, patchField } from "../layoutModel";
import { copyLayoutTo } from "../layoutMatch";
import {
  consentLine,
  draftConsentView,
  needsRunConsent,
  rendererAuthorOf,
  withRendererAuthor,
} from "../useRowLayouts";

vi.mock("@monaco-editor/react", () => ({
  default: () => null,
  Editor: () => null,
}));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: vi.fn(),
  default: vi.fn(),
}));

const parse = (html) => new DOMParser().parseFromString(html, "text/html");

describe("M1 · the wrapper frame", () => {
  const doc = buildFrameDoc(
    '<p id="mine">hi</p>',
    { id: "r1" },
    "Custom renderer of X",
  );
  const wrapper = parse(doc);
  const inner = wrapper.querySelector("iframe");
  it("the wrapper's CSP refuses every frame navigation and allows only what the renderer uses", () => {
    expect(WRAPPER_CSP).toBe(
      `${RENDERER_CSP}; frame-src 'none'; child-src 'none'`,
    );
    const metas = wrapper.querySelectorAll(
      'meta[http-equiv="Content-Security-Policy"]',
    );
    expect(metas).toHaveLength(1);
    expect(metas[0].getAttribute("content")).toBe(WRAPPER_CSP);
    // The CSP comes before the frame in the wrapper's markup.
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(
      doc.indexOf("<iframe"),
    );
  });
  it("the wrapper runs only its reporting script; the renderer frame is sandboxed the same way", () => {
    const scripts = wrapper.querySelectorAll("script");
    expect(scripts).toHaveLength(1);
    expect(scripts[0].textContent).toBe(WRAPPER_SCRIPT);
    expect(WRAPPER_SCRIPT).toContain(
      `document.addEventListener("securitypolicyviolation"`,
    );
    expect(WRAPPER_SCRIPT).toContain("parent.postMessage(");
    // The reporter comes before the frame, so it hears the first refusal.
    expect(doc.indexOf("securitypolicyviolation")).toBeLessThan(
      doc.indexOf("<iframe"),
    );
    expect(wrapper.querySelectorAll("iframe")).toHaveLength(1);
    expect(inner.getAttribute("sandbox")).toBe(RENDERER_SANDBOX);
    expect(inner.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(inner.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(inner.getAttribute("title")).toBe("Custom renderer of X");
  });
  it("the renderer document: its CSP, the bootstrap posting to the app's window, then the author's HTML", () => {
    const src = inner.getAttribute("srcdoc");
    const r = parse(src);
    expect(
      r
        .querySelector('meta[http-equiv="Content-Security-Policy"]')
        .getAttribute("content"),
    ).toBe(RENDERER_CSP);
    const boot = r.querySelector("script").textContent;
    expect(boot).toContain("var app = parent.parent;");
    expect(boot).toContain("app.postMessage(m");
    expect(boot).not.toContain("parent.postMessage(");
    expect(r.getElementById("mine").textContent).toBe("hi");
    expect(src.indexOf("window.fa = fa")).toBeLessThan(
      src.indexOf('id="mine"'),
    );
  });
  it("an author's quote or ampersand can't break out of the renderer's srcdoc", () => {
    const evil = buildFrameDoc(`"><script>top.x=1</script>&amp;`, null);
    const w = parse(evil);
    // Only the wrapper's own script; the author's stays inside the srcdoc.
    expect([...w.querySelectorAll("script")].map((x) => x.textContent)).toEqual(
      [WRAPPER_SCRIPT],
    );
    expect(w.querySelector("iframe").getAttribute("srcdoc")).toContain(
      `"><script>top.x=1</script>&amp;`,
    );
  });
  it("reads the renderer's window inside the wrapper", () => {
    expect(rendererWindow(null)).toBeNull();
    const f = document.createElement("iframe");
    document.body.appendChild(f);
    expect(rendererWindow(f)).toBeNull();
    const child = f.contentDocument.createElement("iframe");
    f.contentDocument.body.appendChild(child);
    expect(rendererWindow(f)).toBe(child.contentWindow);
  });
  it("from the wrapper's window only a refused frame navigation counts", () => {
    const wrapperWin = {};
    const nav = {
      type: MSG.csp,
      directive: "frame-src",
      blocked: "https://example.com/w9-nav?leak=x",
    };
    expect(
      acceptWrapper({ source: wrapperWin, data: nav }, wrapperWin),
    ).toEqual(nav);
    expect(acceptWrapper({ source: {}, data: nav }, wrapperWin)).toBeNull();
    expect(acceptWrapper({ source: wrapperWin, data: nav }, null)).toBeNull();
    for (const data of [
      { ...nav, directive: "connect-src" },
      { type: MSG.propose, column: "expected_reply", value: "x" },
      { type: MSG.height, px: 300 },
    ])
      expect(
        acceptWrapper({ source: wrapperWin, data }, wrapperWin),
      ).toBeNull();
  });
});

describe("M1 · SandboxedRenderer hears only the renderer", () => {
  const base = {
    html: "<p>row</p>",
    height: 240,
    payload: {
      id: "r1",
      number: 1,
      input: {},
      expected: {},
      metadata: {},
      other: {},
      outputs: {},
    },
    columns: [
      { id: "c3", name: "expected_reply", section: "expected", editable: true },
    ],
    cells: {},
    datasetId: "ds-m1",
    layout: { id: "v1", name: "Refund review" },
  };
  const setup = () => {
    render(<SandboxedRenderer {...base} />);
    const frame = screen.getByTestId("layout-renderer-frame");
    const inner = frame.contentDocument.createElement("iframe");
    frame.contentDocument.body.appendChild(inner);
    const send = (source, data) =>
      act(() => {
        window.dispatchEvent(new MessageEvent("message", { data, source }));
      });
    return { frame, renderer: inner.contentWindow, send };
  };
  it("serves the wrapper with the renderer inside", () => {
    const { frame } = setup();
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    const w = parse(frame.getAttribute("srcdoc"));
    expect(w.querySelector("meta").getAttribute("content")).toBe(WRAPPER_CSP);
    expect(w.querySelector("iframe").getAttribute("title")).toBe(
      "Custom renderer of Refund review",
    );
  });
  it("says what the wrapper refused; the wrapper can't propose a change", () => {
    const { frame, renderer, send } = setup();
    send(frame.contentWindow, {
      type: MSG.propose,
      column: "expected_reply",
      value: "from the wrapper",
    });
    expect(screen.queryByTestId("layout-renderer-lines")).toBeNull();
    send(frame.contentWindow, {
      type: MSG.csp,
      directive: "frame-src",
      blocked: "https://example.com/w9-nav",
    });
    expect(screen.getByTestId("layout-renderer-lines").textContent).toBe(
      "Blocked: the renderer tried to reach https://example.com/w9-nav (renderers have no network).",
    );
    // The renderer itself is still heard.
    send(renderer, { type: MSG.propose, column: "nope", value: "x" });
    expect(screen.getByTestId("layout-renderer-lines").textContent).toContain(
      "Refused: column “nope” doesn't exist.",
    );
  });
  it("new code starts with no lines", () => {
    const { rerender } = render(<SandboxedRenderer {...base} />);
    const frame = screen.getAllByTestId("layout-renderer-frame").at(-1);
    const inner = frame.contentDocument.createElement("iframe");
    frame.contentDocument.body.appendChild(inner);
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: MSG.propose, column: "nope", value: "x" },
          source: inner.contentWindow,
        }),
      );
    });
    expect(screen.getByTestId("layout-renderer-lines")).toBeTruthy();
    rerender(<SandboxedRenderer {...base} html="<p>other code</p>" />);
    expect(screen.queryByTestId("layout-renderer-lines")).toBeNull();
  });
});

// ------------------------------------------------------------------ M2
const f = (id, name, value) => ({
  column: { id, name, role: null, data_type: "text", origin_type: "OTHERS" },
  value,
  original: value,
  edited: false,
});
const MODEL = {
  input: [
    f("c1", "customer_message", "my order is late"),
    f("c2", "intent", "refund"),
  ],
  expected: [f("c3", "expected_reply", "We are sorry")],
  metadata: [f("c5", "language", "en")],
  noRole: [],
  outputs: [],
  evals: [],
  human: [],
};
const COLUMNS = layoutColumns(MODEL);
const names = (el) =>
  [...el.querySelectorAll("[data-testid^='layout-field-']")].map((n) =>
    n.getAttribute("data-testid").replace("layout-field-", ""),
  );

describe("M2 · editing a right-side field", () => {
  let spec = { ...defaultLayout(COLUMNS), columns: 2 };
  spec = patchField(spec, "c2", { side: "right" });
  spec = patchField(spec, "c3", { side: "left" });
  const edit = (editingId) => ({
    canEdit: true,
    editingId,
    applyRef: { current: null },
    start: vi.fn(),
    cancel: vi.fn(),
    apply: vi.fn(),
    revert: vi.fn(),
  });
  it("two sides side by side, right fields on the right", () => {
    render(
      <RowLayoutView
        spec={spec}
        columns={COLUMNS}
        model={MODEL}
        edit={edit(null)}
      />,
    );
    expect(names(screen.getByTestId("layout-side-left"))).toEqual([
      "customer_message",
      "expected_reply",
      "language",
    ]);
    expect(names(screen.getByTestId("layout-side-right"))).toEqual(["intent"]);
  });
  it("while the right-side field is edited the sides stack, left then right, and its editor shows and applies", () => {
    const e = edit("c2");
    const { container } = render(
      <RowLayoutView spec={spec} columns={COLUMNS} model={MODEL} edit={e} />,
    );
    // Still both sides, in one column: left fields, then the right ones.
    expect(names(screen.getByTestId("layout-side-right"))).toEqual(["intent"]);
    expect(names(container)).toEqual([
      "customer_message",
      "expected_reply",
      "language",
      "intent",
    ]);
    const card = screen.getByTestId("layout-field-intent");
    const box = within(card).getByRole("textbox");
    fireEvent.change(box, { target: { value: "refund_request" } });
    fireEvent.click(within(card).getByRole("button", { name: /^Apply/ }));
    expect(e.apply).toHaveBeenCalledWith("c2", "refund_request");
  });
});

// ------------------------------------------------------------------ M3
const ASHA = {
  id: "v-asha",
  name: "Asha's renderer",
  mine: false,
  shared: true,
  created_by: "Asha Rao",
  state: {
    layout: {
      ...defaultLayout(COLUMNS),
      renderer: { html: "<p id='ran'>ran</p>", height: 200 },
    },
  },
};

describe("M3 · someone else's renderer keeps its author", () => {
  it("names the author of a renderer the user didn't write", () => {
    expect(rendererAuthorOf(ASHA)).toBe("Asha Rao");
    expect(rendererAuthorOf({ ...ASHA, mine: true })).toBeNull();
    expect(
      rendererAuthorOf({ ...ASHA, state: { layout: defaultLayout(COLUMNS) } }),
    ).toBeNull();
  });
  it("a copy keeps the author, so the copier's own copy still asks before it runs", () => {
    const res = copyLayoutTo(ASHA.state.layout, COLUMNS);
    const spec = withRendererAuthor(res.spec, ASHA);
    expect(spec.renderer).toEqual({
      html: "<p id='ran'>ran</p>",
      height: 200,
      author: "Asha Rao",
    });
    const copy = {
      id: "v-copy",
      mine: true,
      shared: true,
      created_by: "Maya Chen",
      state: { layout: spec },
    };
    expect(needsRunConsent(copy, {})).toBe(true);
    expect(needsRunConsent(copy, { "v-copy": true })).toBe(false);
    // Agreeing to the original doesn't agree to the copy.
    expect(needsRunConsent(copy, { "v-asha": true })).toBe(true);
    expect(consentLine(copy)).toBe(
      "This layout runs code written by Asha Rao, copied here by Maya Chen. It has no network and can only propose changes that you save with a version.",
    );
    // Others on that dataset see the same author, not the copier as the writer.
    expect(consentLine({ ...copy, mine: false })).toContain(
      "written by Asha Rao, copied here by Maya Chen",
    );
  });
  it("a copy of a copy keeps the first author; edited code is the user's", () => {
    const copy = {
      id: "v-copy",
      mine: false,
      created_by: "Maya Chen",
      state: { layout: withRendererAuthor(ASHA.state.layout, ASHA) },
    };
    expect(withRendererAuthor(copy.state.layout, copy).renderer.author).toBe(
      "Asha Rao",
    );
    const edited = {
      ...copy.state.layout,
      renderer: { ...copy.state.layout.renderer, html: "<p>mine now</p>" },
    };
    expect(withRendererAuthor(edited, copy).renderer).toEqual({
      html: "<p>mine now</p>",
      height: 200,
    });
    // The user's own renderer gets no author.
    const own = { ...ASHA, mine: true };
    expect(
      withRendererAuthor(own.state.layout, own).renderer.author,
    ).toBeUndefined();
    // A layout with no renderer is left as is.
    const plain = defaultLayout(COLUMNS);
    expect(withRendererAuthor(plain, ASHA)).toBe(plain);
  });
  it("Customize on a copy keeps the question while the code is unchanged", () => {
    const copy = {
      id: "v-copy",
      mine: true,
      created_by: "Maya Chen",
      state: { layout: withRendererAuthor(ASHA.state.layout, ASHA) },
    };
    const draft = { ...copy.state.layout };
    expect(needsRunConsent(draftConsentView(draft, copy), {})).toBe(true);
  });
  it("RendererSlot asks with the author and the copier before the copy runs", () => {
    const onTrust = vi.fn();
    const copy = {
      id: "v-copy",
      mine: true,
      created_by: "Maya Chen",
      state: { layout: withRendererAuthor(ASHA.state.layout, ASHA) },
    };
    render(
      <RendererSlot
        view={copy}
        trusted={{}}
        onTrust={onTrust}
        html={copy.state.layout.renderer.html}
        height={200}
        payload={{
          id: "r1",
          input: {},
          expected: {},
          metadata: {},
          other: {},
          outputs: {},
        }}
        columns={[]}
        datasetId="ds-m3"
        layout={{ id: "v-copy", name: "Asha's renderer" }}
      />,
    );
    expect(screen.queryByTestId("layout-renderer-frame")).toBeNull();
    expect(screen.getByTestId("layout-renderer-consent").textContent).toContain(
      "This layout runs code written by Asha Rao, copied here by Maya Chen.",
    );
    fireEvent.click(screen.getByTestId("layout-renderer-run"));
    expect(onTrust).toHaveBeenCalledWith("v-copy");
  });
});
