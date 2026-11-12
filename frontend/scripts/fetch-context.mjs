// Reviewed category rules, not a hostname/occurrence allowlist. Unknown fails.
// This is a conservative literal-context gate, not an interprocedural proof.
import { parse } from "acorn";
import { provenanceContext } from "./provenance-context.mjs";

export const ASSET_HOST_PATTERN =
  /(?:fonts\.(?:googleapis|gstatic)\.com|api\.(?:iconify\.design|simplesvg\.com|unisvg\.com)|flagcdn\.com|cdn-icons-png\.flaticon\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com|mintcdn\.com)/i;

export const URL_PATTERN =
  /(?<![a-z\d+/])(?:(?:https?:|wss?:)\/\/[^\s"'`<>\\)\]}]*|\/\/[a-z\d][^\s"'`<>\\)\]}]*)/gi;
const keyOf = (node) => node?.name ?? node?.value;
const callName = (node) =>
  node?.type === "MemberExpression" ? keyOf(node.property) : keyOf(node);
const networkCalls = new Set([
  "fetch",
  "XMLHttpRequest",
  "axios",
  "importScripts",
  "Worker",
  "SharedWorker",
  "EventSource",
  "WebSocket",
  "sendBeacon",
  "loadScript",
  "loadStyleSheet",
]);
const networkKeys = new Set([
  "src",
  "srcset",
  "srcSet",
  "poster",
  "backgroundImage",
  "api_host",
  "apiHost",
  "api_hosts",
  "api_url",
  "apiUrl",
  "baseURL",
  "scriptUrl",
  "workerUrl",
  "recorder_src",
  "recorderSrc",
]);
const namespaces = new Set([
  "http://www.w3.org/1999/xhtml",
  "http://www.w3.org/2000/svg",
  "http://www.w3.org/1999/xlink",
  "http://www.w3.org/XML/1998/namespace",
  "http://www.w3.org/2000/xmlns/",
  "http://www.w3.org/1998/Math/MathML",
]);
const verdict = (category, reason, fail = false) => ({
  category,
  reason,
  fail,
});
const fail = (reason) => verdict("FAIL_FETCH", reason, true);
const unknown = () =>
  verdict(
    "FAIL_UNKNOWN",
    "No reviewed non-fetch context proves this occurrence inert",
    true,
  );

// Inspect markup in HTML, SVG, or an emitted JS string (CSS-in-JS included).
export function markupContext(text, index, url) {
  const before = text.slice(0, index);
  const tagStart = before.lastIndexOf("<");
  const tagEnd = text.indexOf(">", index);
  const tag =
    tagStart > before.lastIndexOf(">") && tagEnd >= 0
      ? text.slice(tagStart, tagEnd + 1)
      : "";
  const attr = /([\w:-]+)\s*=\s*["']?[^"'<>]*$/.exec(before.slice(tagStart));
  if (
    /url\(\s*["']?[^)]*$|@import\s+(?:url\(\s*)?["']?[^;]*$/i.test(
      before.slice(-500),
    )
  )
    return fail("CSS url()/@import resource");
  if (tag && attr) {
    const name = attr[1].toLowerCase();
    if (["src", "srcset", "poster", "data"].includes(name))
      return fail(`Markup ${name} resource`);
    if (name === "href" || name === "xlink:href") {
      if (/^<a\b/i.test(tag))
        return verdict(
          "USER_CLICKED_LINK",
          "Anchor href (no automatic resource load)",
        );
      return fail(
        "Non-anchor href, including stylesheet/preload/icon/manifest and SVG references",
      );
    }
    if (/^xmlns(?::|$)/.test(name) && namespaces.has(url))
      return verdict("XML_NAMESPACE", "XML/SVG namespace declaration");
  }
  return null;
}

function classifyJs(node, parents, value, index, url) {
  const markup = markupContext(value, index, url);
  if (markup) return markup;
  const parent = parents.at(-1);
  const grand = parents.at(-2);
  if (parent?.type === "ImportExpression")
    return fail("Dynamic import resource");
  const assignment =
    parent?.type === "AssignmentExpression" && parent.right === node;
  if (assignment && networkKeys.has(callName(parent.left)))
    return fail("Resource property assignment");
  const property = parent?.type === "Property" && parent.value === node;
  const key = property ? keyOf(parent.key) : "";
  if (networkKeys.has(key)) return fail(`Resource/config property ${key}`);
  if (property && key === "href") {
    const call = parents.at(-3);
    if (
      grand?.type === "ObjectExpression" &&
      call?.type === "CallExpression" &&
      call.arguments[0]?.value === "a" &&
      !grand.properties.some((p) => ["prefetch", "rel"].includes(keyOf(p.key)))
    )
      return verdict(
        "USER_CLICKED_LINK",
        "Emitted React anchor href without prefetch",
      );
    return fail("href requires a proven anchor, never a generic link element");
  }
  if (parent?.type === "CallExpression" || parent?.type === "NewExpression") {
    const name = callName(parent.callee);
    if (networkCalls.has(name)) return fail(`${name} resource argument`);
    if (
      parent.callee?.object?.name === "axios" ||
      (name === "open" && parent.callee?.object?.name !== "window")
    )
      return fail("HTTP client or unproven XMLHttpRequest.open argument");
    if (name === "setAttribute" && networkKeys.has(parent.arguments[0]?.value))
      return fail("Resource setAttribute argument");
    if (name === "open" && parent.callee?.object?.name === "window")
      return verdict(
        "USER_CLICKED_LINK",
        "Explicit window.open URL, inventoried separately from passive assets",
      );
    if (
      ["createElementNS", "setAttributeNS"].includes(name) &&
      parent.arguments[0] === node &&
      namespaces.has(url)
    )
      return verdict(
        "XML_NAMESPACE",
        "DOM namespace argument, not a network URL",
      );
    if (
      (/Error$/.test(name || "") ||
        (parent.callee?.object?.name === "console" &&
          ["warn", "error", "info", "log"].includes(name))) &&
      /\s/.test(value.replace(url, ""))
    )
      return verdict("ERROR_MESSAGE", "Error/console diagnostic prose");
  }
  if (property && /^xmlns(?::|$)/.test(key) && namespaces.has(url))
    return verdict("XML_NAMESPACE", "Namespace property");
  // Monaco language metadata: references[] is rendered as documentation links,
  // never imported as editor code. Require both the array path and name/url pair.
  if (
    property &&
    key === "url" &&
    grand?.type === "ObjectExpression" &&
    grand.properties.some(
      (p) => keyOf(p.key) === "name" && typeof p.value?.value === "string",
    ) &&
    parents.at(-3)?.type === "ArrayExpression" &&
    parents.at(-4)?.type === "Property" &&
    keyOf(parents.at(-4).key) === "references"
  )
    return verdict(
      "DOCUMENTATION_TEXT",
      "Language-service named documentation reference",
    );
  if (
    property &&
    key === "value" &&
    grand?.properties?.some(
      (p) => keyOf(p.key) === "kind" && p.value?.value === "markdown",
    ) &&
    parents.at(-3)?.type === "Property" &&
    ["description", "documentation"].includes(keyOf(parents.at(-3).key))
  )
    return verdict(
      "DOCUMENTATION_TEXT",
      "Language-service Markdown documentation value",
    );
  if (
    property &&
    ["placeholder", "helperText"].includes(key) &&
    grand?.type === "ObjectExpression" &&
    parents.at(-3)?.type === "CallExpression" &&
    ["jsx", "jsxs", "createElement"].includes(callName(parents.at(-3).callee))
  )
    return verdict(
      "DOCUMENTATION_TEXT",
      "Input placeholder/helper text, not a resource prop",
    );
  // Documentation prose must be text content, not a URL-shaped variable or
  // arbitrary string argument that a loader could consume.
  if (
    property &&
    ["children", "description", "message"].includes(key) &&
    value !== url &&
    /\b(?:see|documentation|learn more|read more|visit)\b/i.test(value)
  )
    return verdict(
      "DOCUMENTATION_TEXT",
      "Documentation prose in a text-content property",
    );
  return unknown();
}

export function javascriptOccurrences(text) {
  const comments = [];
  const ast = parse(text, {
    ecmaVersion: "latest",
    sourceType: "module",
    allowReturnOutsideFunction: true,
    onComment: comments,
  });
  const rows = [];
  const isProvenance = provenanceContext(ast);
  function collect(value, offset, context) {
    const urls = [...value.matchAll(URL_PATTERN)];
    // AG Grid constructs its Google-font URL from a scheme template and a
    // separate hostname constant. Do not report the font-host gate green just
    // because those pieces are not one absolute-URL literal.
    const hosts = [
      ...value.matchAll(new RegExp(ASSET_HOST_PATTERN.source, "gi")),
    ].filter(
      (host) =>
        !urls.some(
          (url) =>
            host.index >= url.index && host.index < url.index + url[0].length,
        ),
    );
    for (const match of [...urls, ...hosts]) {
      rows.push({
        url: match[0],
        offset: offset + match.index,
        ...context(match),
      });
    }
  }
  for (const comment of comments) {
    collect(comment.value, comment.start + 2, () =>
      verdict(
        "COMMENT_OR_LICENCE",
        "JavaScript comment/licence, not executable text",
      ),
    );
  }
  function walk(node, parents = []) {
    if (!node || typeof node !== "object") return;
    if (node.type === "Literal" && typeof node.value === "string") {
      collect(node.value, node.start, (match) =>
        isProvenance(node)
          ? verdict(
              "PROVENANCE_METADATA",
              "All constant uses terminate in the mark factory's source/LICENCE metadata, not its src/svg fields",
            )
          : classifyJs(node, parents, node.value, match.index, match[0]),
      );
    } else if (node.type === "TemplateElement") {
      const value = node.value.cooked ?? node.value.raw;
      // Template concatenation has unknown data flow; CSS/HTML sinks still fail.
      collect(value, node.start, (match) =>
        isProvenance(node)
          ? verdict(
              "PROVENANCE_METADATA",
              "Template used only as the mark factory's provenance source",
            )
          : markupContext(value, match.index, match[0]) || unknown(),
      );
    } else if (node.type === "Literal" && node.regex) {
      collect(node.regex.pattern, node.start, () =>
        verdict("REGEX_PATTERN", "Regular-expression pattern, not a URL value"),
      );
    }
    for (const [key, child] of Object.entries(node)) {
      // Property.value is an AST node, unlike Literal.value. Never skip it:
      // emitted React props and loader configuration live under that key.
      if (["start", "end", "loc", "regex"].includes(key)) continue;
      if (Array.isArray(child))
        child.forEach((v) => walk(v, [...parents, node]));
      else if (child && typeof child === "object")
        walk(child, [...parents, node]);
    }
  }
  walk(ast);
  return rows;
}

export function textOccurrences(text, extension) {
  const comments = [
    ...text.matchAll(
      extension === ".css" ? /\/\*[\s\S]*?\*\//g : /<!--[\s\S]*?-->/g,
    ),
  ];
  const urls = [...text.matchAll(URL_PATTERN)];
  const hosts = [
    ...text.matchAll(new RegExp(ASSET_HOST_PATTERN.source, "gi")),
  ].filter(
    (host) =>
      !urls.some(
        (url) =>
          host.index >= url.index && host.index < url.index + url[0].length,
      ),
  );
  return [...urls, ...hosts].map((match) => ({
    url: match[0],
    offset: match.index,
    ...(comments.some(
      (c) => match.index >= c.index && match.index < c.index + c[0].length,
    )
      ? verdict("COMMENT_OR_LICENCE", "Markup/CSS comment")
      : markupContext(text, match.index, match[0]) || unknown()),
  }));
}
