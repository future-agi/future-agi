import { unified } from "unified";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";

export const PURPLE = "#7857FC";
export const REPORT_BADGE = "Research by Falcon AI";

const processor = unified().use(remarkParse).use(remarkGfm);

const STEP = /^(\d{2})\s*[\u2014\u2013-]\s*(.+)$/;
const STEP_PREFIX = /^\d{2}\s*[\u2014\u2013-]\s*/;
const STATS_ROW = /^(.+?)\s*\|\s*(.+)$/;

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function inline(node) {
  switch (node.type) {
    case "text":
      return esc(node.value);
    case "inlineCode":
      return `<code>${esc(node.value)}</code>`;
    case "strong":
      return `<b>${node.children.map(inline).join("")}</b>`;
    case "emphasis":
      return `<i>${node.children.map(inline).join("")}</i>`;
    case "delete":
      return `<s>${node.children.map(inline).join("")}</s>`;
    case "break":
      return "<br>";
    case "link": {
      const safe = /^(https?:\/\/|\/)/i.test(node.url || "") ? esc(node.url) : "";
      const body = node.children.map(inline).join("");
      return safe ? `<a href="${safe}">${body}</a>` : body;
    }
    default:
      return (node.children || []).map(inline).join("");
  }
}

const text = (node) => (node.children || []).map(inline).join("");

const plain = (node) =>
  node.type === "text" || node.type === "inlineCode"
    ? node.value
    : (node.children || []).map(plain).join("");

// The strip is written as a fenced block so it stays valid markdown everywhere else.
function statsBlock(value) {
  const stats = value
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = STATS_ROW.exec(l);
      return m ? { n: m[1].trim(), label: m[2].trim() } : null;
    })
    .filter(Boolean);
  return stats.length ? { type: "stats", stats } : null;
}

function tableBlock(node) {
  const rows = node.children.map((row) => row.children.map(text));
  const [head, ...body] = rows;
  if (!head) return null;
  const align = (node.align || []).map((a) => a || "left");
  return { type: "table", head, rows: body, align };
}

// One quote line per finding, whether the model separated them by a break or a blank line.
function quoteLines(node) {
  const lines = [];
  node.children
    .filter((c) => c.type === "paragraph")
    .forEach((para) => {
      let current = [];
      para.children.forEach((child) => {
        if (child.type === "break") {
          lines.push(current);
          current = [];
          return;
        }
        // A soft line break arrives inside a text node, not as a break of its own.
        if (child.type === "text" && child.value.includes("\n")) {
          const parts = child.value.split("\n");
          parts.forEach((part, i) => {
            if (i) {
              lines.push(current);
              current = [];
            }
            if (part) current.push({ type: "text", value: part });
          });
          return;
        }
        current.push(child);
      });
      lines.push(current);
    });
  return lines.filter((l) => l.length);
}

// A quote whose every line leads with bold reads as a list of findings, not an aside.
function quoteBlock(node) {
  const lines = quoteLines(node);
  const leads = lines.map((children) => {
    const [first, ...rest] = children;
    if (!first || first.type !== "strong") return null;
    return [first.children.map(inline).join(""), rest.map(inline).join("").replace(/^\s+/, "")];
  });
  if (lines.length && leads.every(Boolean)) return { type: "solves", solves: leads };
  return { type: "note", html: lines.map((l) => l.map(inline).join("")).join("<br>") };
}

function listBlock(node) {
  return {
    type: "list",
    ordered: Boolean(node.ordered),
    items: node.children.map((li) =>
      li.children.map((c) => (c.type === "paragraph" ? text(c) : text(c))).join(" "),
    ),
  };
}

function block(node, state) {
  if (node.type === "heading") {
    const body = text(node);
    if (node.depth === 1) return { type: "h1", html: body };
    if (node.depth === 2) {
      const m = STEP.exec(plain(node));
      if (m) return { type: "step", step: m[1], html: body.replace(STEP_PREFIX, "") };
      return { type: "h2", html: body };
    }
    return { type: "h3", html: body };
  }
  if (node.type === "paragraph") {
    const body = text(node);
    if (state.afterTitle) return { type: "subtitle", html: body };
    const only = node.children.length === 1 && node.children[0];
    if (only && only.type === "emphasis") return { type: "muted", html: inline(only) };
    if (node.children[0]?.type === "strong" && !state.lede) return { type: "lede", html: body };
    return { type: "text", html: body };
  }
  if (node.type === "code") {
    if (node.lang === "stats") return statsBlock(node.value);
    if (node.lang === "prompt") return { type: "prompt", value: node.value };
    return { type: "code", value: node.value };
  }
  if (node.type === "table") return tableBlock(node);
  if (node.type === "blockquote") return quoteBlock(node);
  if (node.type === "list") return listBlock(node);
  return null;
}

/**
 * Turn one assistant answer into the pages the report renders.
 * A thematic break starts a new page; everything else is a block on the current one.
 */
export function parseReport(markdown) {
  const tree = processor.parse(markdown || "");
  const pages = [{ blocks: [] }];
  const state = { afterTitle: false, lede: false };
  let title = "";

  tree.children.forEach((node) => {
    if (node.type === "thematicBreak") {
      if (pages[pages.length - 1].blocks.length) pages.push({ blocks: [] });
      return;
    }
    const b = block(node, state);
    state.afterTitle = b?.type === "h1";
    if (b?.type === "h1" && !title) title = b.html.replace(/<[^>]+>/g, "");
    if (b?.type === "lede") state.lede = true;
    if (b) pages[pages.length - 1].blocks.push(b);
  });

  const blocks = pages.flatMap((p) => p.blocks);
  return {
    title: title || "Falcon report",
    pages: pages.filter((p) => p.blocks.length),
    isReport: blocks.some((b) => ["h1", "step", "stats", "table", "evals"].includes(b.type)),
  };
}

// Cheap enough to run on every render: a report opens on a title and carries structure.
export function looksLikeReport(markdown) {
  const md = String(markdown || "");
  return /^#\s+\S/m.test(md) && /^(##\s+\S|\|.*\|)/m.test(md);
}

export const REPORT_CSS = `
@page { size: A4; margin: 14mm 15mm 12mm 15mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Inter, Arial, sans-serif;
  font-size: 9.4pt; line-height: 1.55; color: #161C24; background: #fff; margin: 0; }
.bar { display:flex; align-items:center; justify-content:space-between;
  border-bottom: 2px solid ${PURPLE}; padding-bottom: 7px; margin-bottom: 16px; }
.lock { display:flex; align-items:center; gap:8px; }
svg.g { height: 18px; width: 18px; }
svg.gs { height: 9px; width: 9px; }
.lock img.w { height: 12px; width: 76px; margin-right: 2px; }
.by { display:inline-flex; align-items:center; gap:6px; background:#ECE8FF; color:#5A41BD;
  border-radius:12px; padding:3px 11px 3px 8px; font-size:7.4pt; font-weight:700;
  text-transform:uppercase; letter-spacing:0.6px; }
h1 { font-size: 21pt; margin: 0 0 4px; letter-spacing: -0.8px; font-weight: 700; }
.sub { color: #605C70; font-size: 9.6pt; margin-bottom: 16px; }
h2 { font-size: 11pt; margin: 18px 0 6px; font-weight: 700; letter-spacing: -0.2px;
  page-break-after: avoid; }
h2 .k { color:${PURPLE}; }
h3 { font-size:9.5pt; margin:11px 0 3px; font-weight:700; page-break-after:avoid; }
p { margin: 0 0 10px; }
.lede b { font-weight: 700; }
.muted { font-size:8.2pt; color:#605C70; margin-top:6px; }
ul, ol { margin: 0 0 10px; padding-left: 18px; }
li { margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 4px; font-size: 8.7pt; }
th { text-align: left; padding: 5px 8px; font-weight: 600; font-size: 7.2pt; color: #605C70;
  text-transform: uppercase; letter-spacing: 0.6px; border-bottom: 1.5px solid #161C24; }
td { border-bottom: 1px solid #E1DFEC; padding: 6px 8px; vertical-align: top; }
tr { page-break-inside: avoid; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.2pt;
  background: #F1F0F5; padding: 1px 5px; border-radius: 3px; }
a { color: #5A41BD; text-decoration: none; }
.n { width: 18px; color:${PURPLE}; font-weight:700; }
.tag { display:inline-block; margin-left:6px; padding:1px 6px; border-radius:9px;
  font-size:6.2pt; font-weight:700; text-transform:uppercase; letter-spacing:0.5px;
  vertical-align:1px; }
.t-b { background:#ECE8FF; color:#5A41BD; }
.t-c { background:#F1F0F5; color:#605C70; }
.stat { display:flex; gap:12px; margin:10px 0 14px; }
.sb { flex:1; border-left:3px solid ${PURPLE}; padding:2px 0 2px 12px; }
.sn { font-size:20pt; font-weight:700; letter-spacing:-0.9px; line-height:1.05; }
.sl { font-size:7.6pt; color:#605C70; margin-top:4px; line-height:1.4; }
.mono { white-space:pre-wrap; font-family:"SF Mono",Menlo,Consolas,monospace;
  font-size:6.6pt; background:#F1F0F5; border-radius:5px; padding:10px 12px;
  display:block; line-height:1.55; margin:6px 0; }
.bad { color:#9b2226; }
.solves { border-left:3px solid ${PURPLE}; padding:3px 0 3px 13px; margin:12px 0 0;
  font-size:9pt; }
.solves div { margin-bottom:5px; }
.solves b { color:#5A41BD; }
.note { border-left:3px solid ${PURPLE}; background:#ECE8FF; border-radius:0 5px 5px 0;
  padding:8px 11px; margin:9px 0; font-size:8.4pt; line-height:1.45; }
.prompt { page-break-inside:avoid; background:#F7F8FA; border:1px solid #E1DFEC;
  border-radius:6px; padding:8px 11px; font-family:"SF Mono", Menlo, Consolas, monospace;
  font-size:5.9pt; line-height:1.27; white-space:pre-wrap; margin:5px 0 3px; }
.pgbrk { page-break-before: always; }
.pnum { margin-top:14px; padding-top:8px; border-top:1px solid #E1DFEC;
  font-size:7.4pt; color:#938FA3; text-align:right; }
`;

export const PILL = { "built in": "t-b", custom: "t-c" };
const TRAILING_KIND = /\s*(built in|custom)\s*$/i;

// A cell that ends "built in" or "custom" is naming the eval's kind, not its score.
export function TAG_CELL(html) {
  const m = TRAILING_KIND.exec(String(html));
  if (!m) return html;
  const cls = PILL[m[1].toLowerCase()];
  return `${String(html).slice(0, m.index)}<span class="tag ${cls}">${m[1]}</span>`;
}

const ERROR_LINE = /^(\s*(?:Query error|Validation error|Error)[^\n]*)$/gm;

function printBlock(b) {
  switch (b.type) {
    case "h1":
      return `<h1>${b.html}</h1>`;
    case "subtitle":
      return `<div class="sub">${b.html}</div>`;
    case "step":
      return `<h2><span class="k">${b.step}</span> &nbsp;${b.html}</h2>`;
    case "h2":
      return `<h2>${b.html}</h2>`;
    case "h3":
      return `<h3>${b.html}</h3>`;
    case "lede":
      return `<p class="lede">${b.html}</p>`;
    case "muted":
      return `<p class="muted">${b.html}</p>`;
    case "note":
      return `<div class="note">${b.html}</div>`;
    case "solves":
      return `<div class="solves">${b.solves
        .map(([a, t]) => `<div><b>${a}</b> ${t}</div>`)
        .join("")}</div>`;
    case "stats":
      return `<div class="stat">${b.stats
        .map((s) => `<div class="sb"><div class="sn">${esc(s.n)}</div><div class="sl">${esc(s.label)}</div></div>`)
        .join("")}</div>`;
    case "prompt":
      return `<div class="prompt">${esc(b.value)}</div>`;
    case "code":
      return `<span class="mono">${esc(b.value).replace(ERROR_LINE, '<span class="bad">$1</span>')}</span>`;
    case "list": {
      const tag = b.ordered ? "ol" : "ul";
      return `<${tag}>${b.items.map((i) => `<li>${i}</li>`).join("")}</${tag}>`;
    }
    case "table": {
      const indexed = !b.head[0];
      const cell = (c, i) => {
        const cls = indexed && !i ? ' class="n"' : "";
        return `<td${cls}>${TAG_CELL(c)}</td>`;
      };
      return `<table><tr>${b.head.map((h) => `<th>${h}</th>`).join("")}</tr>${b.rows
        .map((r) => `<tr>${r.map(cell).join("")}</tr>`)
        .join("")}</table>`;
    }
    default:
      return `<p>${b.html}</p>`;
  }
}

/**
 * The print document. Same stylesheet and same markup the reference pack was rendered
 * from, so the browser's own print engine reproduces it rather than approximating it.
 */
export function toPrintHtml(doc, { glyphSvg = "", wordmarkDataUri = "", badge = REPORT_BADGE } = {}) {
  const mark = (cls) =>
    glyphSvg.replace(
      /<svg[^>]*?>/,
      `<svg class="${cls}" viewBox="0 0 51 51" fill="${PURPLE}" xmlns="http://www.w3.org/2000/svg">`,
    );
  const chip = badge ? `<span class="by">${mark("gs")}${esc(badge)}</span>` : "";
  const header =
    `<div class="bar"><div class="lock">${mark("g")}` +
    `<img class="w" src="${wordmarkDataUri}"></div><div>${chip}</div></div>`;
  const n = doc.pages.length;
  const body = doc.pages
    .map(
      (p, i) =>
        (i ? '<div class="pgbrk"></div>' : "") +
        header +
        p.blocks.map(printBlock).join("") +
        `<div class="pnum">Page ${i + 1} of ${n}</div>`,
    )
    .join("");
  return `<!doctype html>\n<html><head><meta charset="utf-8"><title>${esc(doc.title)}</title><style>${REPORT_CSS}</style></head><body>\n${body}\n</body></html>\n`;
}
