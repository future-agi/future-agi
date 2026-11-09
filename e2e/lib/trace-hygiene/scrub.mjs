// The trace scrubber's rules over trace EVENTS and values (708 add.78).
// Structural: header and field NAMES, the SENSITIVE_ROUTES whose bodies are
// dropped, URL tokens, password-typed inputs and the actions that targeted
// them, and the values those inputs held (propagated to every other string,
// e.g. a step title `Fill "<value>"`). Shapes (JWT, auth schemes) are removed
// too, as defence in depth. The independent auditor
// (e2e/scripts/evidence-audit/) shares none of this.
import {
  REDACTED,
  scrubUrlTokens,
  sensitiveUrl,
  thirdPartyUrl,
} from "./routes.mjs";

// Bodies never kept: the credential endpoints, and every third-party request.
const dropBodies = (url) => sensitiveUrl(url) || thirdPartyUrl(url);

// Credential-bearing NAMES, by CONTAINMENT (708 add.78 r2): a key, field,
// parameter or header name is broken at camelCase humps and separators and
// lower-cased; it is sensitive when its letters run together contain one of
// STEMS (loginPasswordValue, x_access_token, csrfmiddlewaretoken), or when one
// of its parts, trailing digits aside, is one of PART_STEMS (stems too short to
// match inside other words: snapshotPath, author). Structural exclusions, kept
// deliberately NARROWER than the independent auditor's so that the scrubber
// removes at least every name the auditor would flag: the last part names the
// credential's metadata (an id, type, count, name, ...), or the first part asks
// about it (is_, has_). Parts that only contain a stem (tokenizer) are skipped.
const STEMS = [
  "password",
  "passwd",
  "passphrase",
  "passcode",
  "secret",
  "token",
  "apikey",
  "accesskey",
  "privatekey",
  "signingkey",
  "sessionkey",
  "sessionid",
  "csrf",
  "xsrf",
  "credential",
  "authorization",
  "cookie",
  "bearer",
  "jwt",
  "recoverycode",
  "backupcode",
  "signature",
  "magiclink",
  "invitelink",
  "inviteurl",
  "invitetoken",
  "invitationlink",
  "invitationurl",
  "invitationtoken",
  "resetlink",
  "reseturl",
  "resettoken",
  "verifylink",
  "verifyurl",
  "verifytoken",
  "verificationlink",
  "verificationurl",
  "verificationtoken",
  "storagestate",
];
const PART_STEMS = ["pass", "pwd", "otp", "totp", "access", "refresh", "auth"];
const DESCRIBES_LAST = [
  "id",
  "ids",
  "uuid",
  "type",
  "kind",
  "count",
  "length",
  "size",
  "name",
  "label",
  "title",
  "required",
  "enabled",
  "disabled",
  "policy",
  "version",
  "at",
  "expires",
  "expiry",
  "status",
];
const ASKS_FIRST = ["is", "has", "should", "can", "requires", "allow"];
const ONLY_CONTAINS = ["tokenizer", "tokenize", "tokenized", "passwordless"];

/** True when a field/key/parameter/header NAME is credential-bearing (see STEMS). */
export function sensitiveName(name) {
  if (typeof name !== "string" || name === "") return false;
  const parts = name
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase()
    .split(/[^a-z\d]+/)
    .filter((p) => p !== "" && !ONLY_CONTAINS.includes(p));
  if (parts.length === 0) return false;
  if (
    parts.length > 1 &&
    (DESCRIBES_LAST.includes(parts[parts.length - 1]) ||
      ASKS_FIRST.includes(parts[0]))
  )
    return false;
  const letters = parts.join("").replace(/\d/g, "");
  return (
    STEMS.some((s) => letters.includes(s)) ||
    parts.some((p) => PART_STEMS.includes(p.replace(/\d+$/, "")))
  );
}

export const SENSITIVE_FIELD = {
  test: (name) => /^httpcredentials$/i.test(name) || sensitiveName(name),
};
export const SENSITIVE_HEADER =
  /^(?:authorization|proxy-authorization|cookie|set-cookie|api-?key|x-api-key|x-secret-key|x-csrftoken|x-[a-z0-9-]*-(?:key|token|secret|signature|auth))$/i;
export const PASSWORD_SELECTOR =
  /pass(?:word|wd)?|pwd|secret|api[-_ ]?key|token|otp|one[-_ ]time|credential/i;
const BODY_PARAMS = [
  "postData",
  "jsonData",
  "formData",
  "multipartData",
  "data",
];
const FILL_METHODS =
  /^(?:fill|type|pressSequentially|insertText|setInputValue)$/;
// Only values this long are replaced wherever else they appear (a short value
// would rewrite unrelated text); shorter ones are still redacted where typed.
const PROPAGATE = 8;

const JWT = /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g;
const AUTH_SCHEME =
  /\b(Bearer|Basic|Token|Digest)(\s+)(?!\[REDACTED)[A-Za-z0-9._~+/=-]{6,}/g;
// Inline `name: "value"`, `name = 'value'`, `\"name\":\"value\"` and
// `name=value` in free text: any name, kept when sensitiveName() says so.
const INLINE_QUOTED =
  /(["']?)([A-Za-z0-9_.-]{1,64})\1(\s*[:=]\s*)(["'])((?:(?!\4)[^\\]|\\.)*)\4/g;
const INLINE_ESCAPED =
  /(\\"([A-Za-z0-9_.-]{1,64})\\"\s*:\s*\\")((?:[^"\\]|\\[^"])*)(\\")/g;
const INLINE_FORM =
  /(^|[^A-Za-z0-9_.-])([A-Za-z0-9_.-]{1,64})(\s*=\s*)([^&\s"',;<>]+)/g;
const HEADER_LINE =
  /((?:^|\n|\\n)[ \t]*((?:proxy-)?authorization|(?:set-)?cookie|x-[a-z0-9-]+)[ \t]*:[ \t]*)([^\n\\]+)/gi;
const sensitiveHeaderOrName = (name) =>
  SENSITIVE_HEADER.test(name) || sensitiveName(name);

export const isRedacted = (v) =>
  typeof v === "string" && v.startsWith("[REDACTED");

/** A fresh scrub context: the password values found so far and the stats. */
export function newContext() {
  return {
    passwords: new Set(),
    passwordCalls: new Set(),
    dropped: new Set(),
    textResources: new Set(),
    stats: {},
  };
}

const count = (ctx, what) => (ctx.stats[what] = (ctx.stats[what] ?? 0) + 1);

/**
 * Inline credential pairs in free text. A pair whose name is not sensitive is
 * kept, but its value is searched again (a pair can sit inside another's value).
 */
function inlinePairs(s, depth) {
  if (depth > 4 || s.length < 3) return s;
  const inner = (v) => inlinePairs(v, depth + 1);
  let out = s.replace(INLINE_QUOTED, (m, q, name, sep, vq, value) =>
    sensitiveName(name) && value && !isRedacted(value)
      ? `${q}${name}${q}${sep}${vq}${REDACTED}${vq}`
      : `${q}${name}${q}${sep}${vq}${inner(value)}${vq}`,
  );
  out = out.replace(INLINE_ESCAPED, (m, head, name, value, tail) =>
    sensitiveName(name) && value && !isRedacted(value)
      ? `${head}${REDACTED}${tail}`
      : `${head}${inner(value)}${tail}`,
  );
  return out.replace(INLINE_FORM, (m, before, name, sep, value) =>
    sensitiveName(name) && !isRedacted(value)
      ? `${before}${name}${sep}${REDACTED}`
      : `${before}${name}${sep}${inner(value)}`,
  );
}

/** String rules: URL tokens, auth schemes, JWTs, inline credential pairs, known password values. */
export function scrubString(s, ctx) {
  if (typeof s !== "string" || s.length < 6) return s;
  let out = scrubUrlTokens(s);
  out = out
    .replace(AUTH_SCHEME, `$1$2${REDACTED}`)
    .replace(JWT, "[REDACTED:jwt]");
  out = inlinePairs(out, 0);
  out = out.replace(HEADER_LINE, (m, head, name) =>
    sensitiveHeaderOrName(name) ? `${head}${REDACTED}` : m,
  );
  for (const v of ctx.passwords)
    if (out.includes(v)) out = out.split(v).join(REDACTED);
  if (out !== s) count(ctx, "strings");
  return out;
}

/** Deep rules over any JSON value; `key` is the name it sits under. */
export function scrubValue(value, ctx, key = "") {
  if (typeof value === "string") {
    if (
      key &&
      (SENSITIVE_FIELD.test(key) || SENSITIVE_HEADER.test(key)) &&
      value &&
      !isRedacted(value)
    ) {
      count(ctx, "fields");
      return REDACTED;
    }
    return scrubString(value, ctx);
  }
  if (Array.isArray(value)) {
    if (key === "cookies")
      return value.map((c) =>
        c && typeof c === "object"
          ? { ...scrubValue(c, ctx), value: REDACTED }
          : REDACTED,
      );
    return value.map((v) => scrubValue(v, ctx));
  }
  if (!value || typeof value !== "object") return value;
  const out = {};
  const recordName =
    typeof value.name === "string"
      ? value.name
      : typeof value.k === "string"
        ? value.k
        : null;
  const sensitiveRecord =
    recordName !== null &&
    (SENSITIVE_HEADER.test(recordName) || SENSITIVE_FIELD.test(recordName));
  for (const [k, v] of Object.entries(value)) {
    if (
      sensitiveRecord &&
      (k === "value" || k === "v") &&
      v !== null &&
      v !== ""
    ) {
      count(ctx, "records");
      out[k] = k === "v" && typeof v === "object" ? { s: REDACTED } : REDACTED;
    } else if (
      (SENSITIVE_FIELD.test(k) || SENSITIVE_HEADER.test(k)) &&
      v !== null &&
      typeof v === "object" &&
      // HAR/trace cookie arrays keep their shape: scrubValue redacts each value.
      !(k === "cookies" && Array.isArray(v))
    ) {
      count(ctx, "fields");
      out[k] = REDACTED;
    } else out[k] = scrubValue(v, ctx, k);
  }
  return out;
}

const PASSWORD_INPUT = (attrs) =>
  attrs &&
  typeof attrs === "object" &&
  !Array.isArray(attrs) &&
  (/^password$/i.test(attrs.type ?? "") ||
    /^(?:current-password|new-password|one-time-code)$/i.test(
      attrs.autocomplete ?? "",
    ) ||
    PASSWORD_SELECTOR.test(
      `${attrs.name ?? ""} ${attrs.id ?? ""} ${attrs["aria-label"] ?? ""}`,
    ));

/** Walk a DOM snapshot node tree ([name, attrs, ...children]); `visit(name, attrs)` per element. */
function walkNodes(node, visit) {
  if (!Array.isArray(node) || typeof node[0] !== "string") return;
  const attrs =
    node[1] && typeof node[1] === "object" && !Array.isArray(node[1])
      ? node[1]
      : null;
  visit(node[0], attrs);
  for (const child of node.slice(attrs ? 2 : 1)) walkNodes(child, visit);
}

/** Pass 1: the password values and password-targeted calls an event reveals. */
export function collect(event, ctx) {
  if (!event || typeof event !== "object") return;
  if (event.type === "frame-snapshot") {
    walkNodes(event.snapshot?.html, (name, attrs) => {
      if (!/^input$/i.test(name) || !PASSWORD_INPUT(attrs)) return;
      for (const k of ["__playwright_value_", "value"])
        if (
          typeof attrs[k] === "string" &&
          attrs[k].length >= PROPAGATE &&
          !isRedacted(attrs[k])
        )
          ctx.passwords.add(attrs[k]);
      // The snapshotter marks the action's target with an empty __playwright_target__.
      if ("__playwright_target__" in attrs && event.snapshot?.callId)
        ctx.passwordCalls.add(event.snapshot.callId);
    });
  }
  if (
    event.type === "before" &&
    event.params &&
    typeof event.params === "object"
  ) {
    const { selector, value, text } = event.params;
    if (typeof selector === "string" && PASSWORD_SELECTOR.test(selector))
      for (const v of [value, text])
        if (typeof v === "string" && v.length >= PROPAGATE && !isRedacted(v))
          ctx.passwords.add(v);
  }
  if (event.type === "resource-snapshot") {
    const s = event.snapshot ?? {};
    if (dropBodies(s.request?.url)) {
      for (const sha1 of [
        s.request?.postData?._sha1,
        s.response?.content?._sha1,
      ])
        if (sha1) ctx.dropped.add(sha1);
    } else {
      for (const part of [s.request?.postData, s.response?.content])
        if (
          part?._sha1 &&
          /json|text\/(?!css|javascript)|xml|html|x-www-form-urlencoded/i.test(
            part.mimeType ?? "",
          )
        )
          ctx.textResources.add(part._sha1);
    }
  }
  if (event.type === "after")
    for (const a of event.attachments ?? [])
      if (a?.sha1 && /json|text|xml|html/i.test(a.contentType ?? ""))
        ctx.textResources.add(a.sha1);
}

function scrubBody(params, ctx) {
  for (const k of BODY_PARAMS) {
    if (params[k] === undefined || params[k] === null || isRedacted(params[k]))
      continue;
    params[k] = REDACTED;
    count(ctx, "sensitiveBodies");
  }
}

/** Base64 JSON/text (runner step previews of postData): scrubbed and re-encoded. */
function scrubBase64Text(b64, ctx) {
  const text = Buffer.from(b64, "base64").toString("utf8");
  if (
    Buffer.from(text, "utf8").toString("base64").replace(/=+$/, "") !==
    b64.replace(/=+$/, "")
  )
    return null;
  return Buffer.from(scrubText(text, ctx), "utf8").toString("base64");
}

/** JSON text through scrubValue, other text through scrubString. */
export function scrubText(text, ctx) {
  try {
    return JSON.stringify(scrubValue(JSON.parse(text), ctx));
  } catch {
    return scrubString(text, ctx);
  }
}

/** Pass 2: one trace event (context trace, network, or runner test.trace), in place. */
export function scrubEvent(event, ctx) {
  if (!event || typeof event !== "object") return event;
  const params =
    event.params && typeof event.params === "object" ? event.params : null;
  if (event.type === "before" && params) {
    if (dropBodies(params.url)) scrubBody(params, ctx);
    else {
      if (typeof params.jsonData === "string")
        params.jsonData = scrubText(params.jsonData, ctx);
      // The context trace records Buffer bodies as "<Buffer>"; the runner's preview is base64.
      if (
        typeof params.postData === "string" &&
        params.postData !== "<Buffer>" &&
        !isRedacted(params.postData)
      ) {
        const re = scrubBase64Text(params.postData, ctx);
        if (re === null) scrubBody(params, ctx);
        else params.postData = re;
      }
    }
    const targeted =
      ctx.passwordCalls.has(event.callId) ||
      (typeof params.selector === "string" &&
        PASSWORD_SELECTOR.test(params.selector));
    if (
      targeted &&
      (FILL_METHODS.test(event.method ?? "") ||
        "value" in params ||
        "text" in params)
    )
      for (const k of ["value", "text"])
        if (typeof params[k] === "string" && params[k]) params[k] = REDACTED;
  }
  if (event.type === "frame-snapshot")
    walkNodes(event.snapshot?.html, (name, attrs) => {
      if (!/^input$/i.test(name) || !PASSWORD_INPUT(attrs)) return;
      for (const k of ["__playwright_value_", "value"])
        if (typeof attrs[k] === "string" && attrs[k]) attrs[k] = REDACTED;
    });
  if (
    event.type === "resource-snapshot" &&
    dropBodies(event.snapshot?.request?.url)
  ) {
    const { request = {}, response = {} } = event.snapshot;
    if (Array.isArray(request.queryString) && thirdPartyUrl(request.url))
      request.queryString = request.queryString.map((q) => ({
        ...q,
        value: REDACTED,
      }));
    if (request.postData)
      request.postData = {
        mimeType: request.postData.mimeType,
        text: REDACTED,
        params: [],
      };
    if (response.content)
      response.content = {
        size: -1,
        mimeType: response.content.mimeType,
        text: REDACTED,
      };
    event.snapshot._hygiene = sensitiveUrl(request.url)
      ? "sensitive-endpoint: bodies dropped"
      : "third-party: query and bodies dropped";
    count(ctx, "sensitiveEndpoints");
  }
  if (
    (event.type === "stdout" || event.type === "stderr") &&
    typeof event.base64 === "string"
  ) {
    const re = scrubBase64Text(event.base64, ctx);
    event.base64 = re ?? REDACTED;
  }
  if (event.type === "after")
    for (const a of event.attachments ?? [])
      if (
        typeof a?.base64 === "string" &&
        /json|text|xml|html/i.test(a.contentType ?? "")
      ) {
        const re = scrubBase64Text(a.base64, ctx);
        a.base64 = re ?? Buffer.from(REDACTED).toString("base64");
      }
  const scrubbed = scrubValue(event, ctx);
  for (const k of Object.keys(event)) delete event[k];
  return Object.assign(event, scrubbed);
}
