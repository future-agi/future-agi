// The evidence auditor's detectors (708 add.78). Broad and INDEPENDENT of the
// trace scrubber (e2e/lib/trace-hygiene/): this file imports nothing from it
// and keeps its own vocabulary. It looks for credential SHAPES (JWTs, auth
// schemes, provider key prefixes, cookie pairs, password-field values, URL
// tokens), credential-named pairs, high-entropy hex/base64url tokens and the
// run's own minted values, through the encodings evidence uses (JSON escaping,
// percent-encoding, base64). A finding carries a kind, an offset and the
// nearest field NAME; the value itself never leaves this module except as a
// per-report keyed digest (audit.mjs).

const PLACEHOLDER =
  /^(?:\[REDACTED[^\]]*\]|<[^<>]*>|[:{$<%].*|\*+|•+|x{4,}|X{4,}|\.\.\.|null|undefined|none|true|false|redacted)$/i;
const MASKED = /\*{3,}|•{2,}|\.{3}|…/;
const WORDS = /^[A-Za-z]+(?:[ _.-][A-Za-z]+)*$/;

export function entropy(s) {
  const counts = new Map();
  for (const c of s) counts.set(c, (counts.get(c) ?? 0) + 1);
  let h = 0;
  for (const n of counts.values())
    h -= (n / s.length) * Math.log2(n / s.length);
  return h;
}

// Only an unquoted identifier list is code. Validate the SERIALIZED contents
// before decoding or splitting anything: quoted values can contain whitespace,
// commas and brackets that belong to a credential, not to its list separators.
// Unsupported list syntax fails closed rather than falling through to the
// scalar prose/code exclusions. A safe item cannot exempt its neighbours.
const BRACKETED_LIST = /^\[([\s\S]*)\]$/;
// Nested JSON can expose multiple backslashes, or a backslash followed by
// decoded whitespace, at an intermediate audit layer. These are separators
// only where the complete identifier-list grammar permits whitespace.
const LIST_SPACE = String.raw`(?:\s|\\+(?:[nrt]|u(?:000[9aAdD]|0020)|\s))*`;
const LIST_IDENTIFIER = String.raw`[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*`;
const IDENTIFIER_LIST = new RegExp(
  `^${LIST_SPACE}(?:${LIST_IDENTIFIER}${LIST_SPACE}(?:,${LIST_SPACE}${LIST_IDENTIFIER}${LIST_SPACE})*)?$`,
);

/** A value that could be a credential: not prose, a placeholder, a mask, a number, a timestamp or code. */
export function credentialLike(raw) {
  if (typeof raw !== "string") return false;
  // A value read out of raw JSON text ends before the escape of a line end
  // (`tokens = [REDACTED]\n    if ...` in an eval template's source).
  const v = raw.trim().replace(/(?:\\[nrt])+$/, "");
  if (v.length > 8192 || PLACEHOLDER.test(v)) return false;
  const list = BRACKETED_LIST.exec(v);
  if (list) {
    if (!IDENTIFIER_LIST.test(list[1])) return true;
    // Validation proves every escape is separator whitespace and every comma
    // is BETWEEN complete identifiers. Never normalize unsupported scalars.
    return list[1]
      .replace(/\\+(?:[nrt]|u(?:000[9aAdD]|0020)|\s)/g, " ")
      .split(",")
      .some((item) => credentialLike(item.trim()));
  }
  if (MASKED.test(v) || v.length < 6 || /\s/.test(v) || /^\d+$/.test(v))
    return false;
  // Decimal strings (DRF DecimalField prices and costs) and ISO dates/times.
  if (/^[+-]?(?:\d+\.\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(v)) return false;
  if (
    /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.test(
      v,
    )
  )
    return false;
  if (WORDS.test(v) && v.length < 32) return false;
  // Source-code expressions (error-context code frames, sources, eval-template
  // code served as JSON): calls, blocks, template literals and arrows, and a
  // bracketed list of identifiers none of which is credential-like alone.
  if (/[`(){}]|\$\{|=>/.test(v)) return false;
  return true;
}

// The auditor's own reading of credential-bearing NAMES (fields, headers,
// params), by CONTAINMENT: the name is split into words (camelCase humps, any
// separator), lower-cased, and it is credential-bearing when
//   - the words run together CONTAIN one of CONTAINED (loginPasswordValue,
//     x-api-key, csrfmiddlewaretoken, new_password2), or
//   - one word, trailing digits aside, IS one of WHOLE (short stems that are
//     substrings of ordinary words: snapshotPath, author, accessibility);
// unless a STRUCTURAL exclusion says the name describes a credential rather
// than holding one: its last word is metadata (api_key_id, token_type,
// passwordPolicy, prompt_tokens_count), a locator (token_endpoint; invite and
// reset links excepted), or its first word is a predicate or a bound (is_,
// has_, max_tokens). Every word keeps its position: deleting even a seemingly
// harmless word can hide api_tokens or change the last-word rule. Words that
// merely contain a stem (tokenizer) delimit containment matches; they are not
// removed from the name. Plural tokens is a credential stem, not an exclusion.
// Bare `tokens = [identifier]` is handled as a value/context exception below.
// Maintained independently of the trace scrubber's own list
// (e2e/lib/trace-hygiene/scrub.mjs), whose exclusions are narrower.
const CONTAINED =
  /password|passwd|passphrase|passcode|secret|token|apikey|accesskey|privatekey|signingkey|sessionkey|sessionid|csrf|xsrf|credential|authorization|cookie|bearer|jwt|recoverycode|backupcode|signature|magiclink|invit(?:e|ation)(?:link|url|token)|reset(?:link|url|token)|verif(?:y|ication)(?:link|url|token)/;
const WHOLE = new Set([
  "pass",
  "pwd",
  "otp",
  "totp",
  "access",
  "refresh",
  "auth",
]);
// The JWT pair's own names: credential-bearing as the LAST word (access,
// jwt_refresh, workspace_access), not as a qualifier (access-control-*,
// refresh_interval).
const LAST_ONLY = new Set(["access", "refresh"]);
const ALWAYS =
  /magiclink|invit(?:e|ation)(?:link|url|token)|reset(?:link|url|token)|verif(?:y|ication)(?:link|url|token)/;
const NOT_A_STEM = new Set([
  "tokenizer",
  "tokenizers",
  "tokenize",
  "tokenized",
  "tokenization",
  "detokenize",
  "passwordless",
  "secretary",
]);
const META_LAST = new Set(
  (
    "id ids uuid guid pk type types kind count counts total len length size at on time ts date " +
    "expiry expires expiration ttl age lifetime name names label labels title hint hints help " +
    "description desc placeholder policy policies rule rules required enabled disabled enforced " +
    "set configured present exists available status strength score min max limit limits version " +
    "field fields mode format algorithm alg provider method methods ref prefix suffix last4 level " +
    "interval usage rate ratio cost price error errors message env owner hash digest sha sha256 " +
    "fingerprint checksum"
  ).split(" "),
);
const LOCATOR_LAST = new Set(
  "url urls uri endpoint endpoints link links path host domain".split(" "),
);
const PREDICATE_FIRST = new Set(
  (
    "is has have should can need needs require requires allow allows show use uses enable with " +
    "without no num max min total supports supported"
  ).split(" "),
);

function nameWords(name) {
  return String(name)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

const CREDENTIAL_HEADER = /^x-[a-z0-9-]*-(?:key|token|secret|signature|auth)$/i;
const QUERY_ONLY =
  /^(?:code|sig|key|ticket|uid|uidb64|samlresponse|samlrequest|state)$/i;

export function credentialName(name) {
  if (typeof name !== "string" || !name) return false;
  if (CREDENTIAL_HEADER.test(name)) return true;
  const words = nameWords(name);
  if (!words.length) return false;
  const bare = (w) => w.replace(/\d+$/, "");
  const joined = words.join("");
  if (words.length > 1) {
    const last = bare(words[words.length - 1]);
    if (PREDICATE_FIRST.has(words[0]) || META_LAST.has(last)) return false;
    if (LOCATOR_LAST.has(last) && !ALWAYS.test(joined)) return false;
  }
  // Do not join across a non-stem word (api_tokenizer_key is not api_key),
  // and do not remove it when determining qualifier/last-word positions.
  let segment = "";
  for (const word of words) {
    if (NOT_A_STEM.has(word)) segment = "";
    else {
      segment += word;
      if (CONTAINED.test(segment)) return true;
    }
  }
  const last = words.length - 1;
  return words.some(
    (w, i) => WHOLE.has(bare(w)) && (!LAST_ONLY.has(bare(w)) || i === last),
  );
}

// Only the exact bare name gets the plural-token code-list exception. Other
// credential names retain the existing code-expression handling, but qualified
// plurals must not inherit the bare `tokens = [keywords]` exemption.
function credentialValue(name, raw) {
  const v = raw.trim().replace(/(?:\\[nrt])+$/, "");
  if (
    name !== "tokens" &&
    nameWords(name).includes("tokens") &&
    BRACKETED_LIST.test(v) &&
    !PLACEHOLDER.test(v)
  )
    return true;
  return credentialLike(raw);
}

// Positions where a high-entropy string is an identifier or content digest.
const ID_NAME =
  /^(?:_?sha1|sha256|sha512|sha|digest|hash|checksum|etag|integrity|fingerprint|realhead|producthead|head|commit|rev|revision|ref|image|imageid|imagedigest|id|uuid|guid|traceparent|tracestate|sentry-trace|baggage|x-b3-traceid|x-amzn-trace-id|x-cloud-trace-context|version|build|.+[-_]id|.+id|.+[-_]ids|.+ids|.+[-_]?sha\d*|.+[-_]?digest|.+[-_]?hash)$/i;

/** The field/header/param name whose value contains `index`, or ''. */
export function nameBefore(text, index) {
  const w = text.slice(Math.max(0, index - 320), index);
  let m = /"name"\s*:\s*"([^"\\]{1,64})"\s*,\s*"value"\s*:\s*"[^"]*$/.exec(w);
  if (m) return m[1];
  m = /"([^"\\]{1,64})"\s*:\s*"[^"]*$/.exec(w);
  if (m) return m[1];
  m = /[?&;#\s]([A-Za-z0-9_.-]{1,64})=[^&#\s"]*$/.exec(w);
  if (m) return m[1];
  m = /(?:^|\n)([A-Za-z0-9-]{1,64}):[ \t]*[^\n]*$/.exec(w);
  return m ? m[1] : "";
}

const safeName = (s) => (s || "").replace(/[^A-Za-z0-9_.@-]/g, "").slice(0, 48);

function* matches(re, text) {
  re.lastIndex = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    yield m;
    if (m[0].length === 0) re.lastIndex++;
  }
}

const unjson = (s) => s.replace(/\\(["\\/])/g, "$1");

function shapeHits(text, out) {
  for (const m of matches(
    /\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{10,}/g,
    text,
  ))
    out.push({
      kind: "jwt",
      offset: m.index,
      value: m[0],
      name: nameBefore(text, m.index),
    });
  for (const m of matches(
    // Scheme names as headers write them; lowercase "token <word>" is prose.
    /\b([Bb]earer|Token|Digest)[ ]+([A-Za-z0-9._~+/=-]{8,})/g,
    text,
  ))
    if (credentialLike(m[2]))
      out.push({
        kind: "auth-scheme",
        offset: m.index,
        value: m[2],
        name: m[1],
      });
  for (const m of matches(
    /\bBasic[ ]+([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])/g,
    text,
  )) {
    const decoded = Buffer.from(m[1], "base64").toString("latin1");
    if (/^[\x20-\x7e]{1,256}:[\x20-\x7e]*$/.test(decoded))
      out.push({
        kind: "auth-scheme",
        offset: m.index,
        value: m[1],
        name: "Basic",
      });
  }
  const prefixes = [
    /\b(?:sk|rk)[-_](?:live|test|proj|ant|or|agentcc|fi)?[-_]?[A-Za-z0-9_]{16,}/g,
    /\bfi[-_][A-Za-z0-9]{24,}/g,
    /\bAKIA[0-9A-Z]{16}\b/g,
    /\bAIza[0-9A-Za-z_-]{35}\b/g,
    /\bgh[pousr]_[A-Za-z0-9]{30,}\b/g,
    /\bgithub_pat_[A-Za-z0-9_]{40,}/g,
    /\bxox[baprs]-[A-Za-z0-9-]{10,}/g,
    /\bhf_[A-Za-z0-9]{30,}/g,
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/g,
    /otpauth:\/\/[^\s"'<>]*secret=[A-Z2-7]{8,}/gi,
  ];
  for (const re of prefixes)
    for (const m of matches(re, text))
      if (entropy(m[0]) >= 3)
        out.push({
          kind: "known-prefix",
          offset: m.index,
          value: m[0],
          name: nameBefore(text, m.index),
        });
}

// JSON pairs, read two ways. Neither consumes the value's closing quote, so a
// pair may start at it: in {"body":"{\"k\":\"v\"}"} the escaped quote that ends
// "body"'s first reading is the opening quote of the inner key.
//   - ESCAPE-AWARE: the value is the whole JSON string, escaped quotes included
//     ({"password":"Q\"..."}).
//   - ONE LEVEL OF JSON IN A JSON STRING: key and value quotes may be escaped,
//     and an escaped quote ends the value.
// Deeper JSON in JSON is read in the JSON-unescaped layers audit.mjs scans,
// each step bounded by LIMITS.normalize (a refusal when reached).
const JSON_PAIRS = [
  /"([A-Za-z0-9_.-]{1,64})"\s*:\s*"((?:[^"\\]|\\.){1,8192})(?=")/g,
  /\\?"([A-Za-z0-9_.-]{1,64})\\?"\s*:\s*\\?"((?:[^"\\]|\\.){1,8192}?)(?=\\?")/g,
];

function pairHits(text, code, out) {
  for (const re of JSON_PAIRS)
    for (const m of matches(re, text)) {
      if (m[1] === "name" || m[1] === "value" || !credentialName(m[1]))
        continue;
      const v = unjson(m[2]);
      if (credentialValue(m[1], v))
        out.push({
          kind: "credential-field",
          offset: m.index,
          value: v,
          name: m[1],
        });
    }
  // {"name": "<header/param/cookie>", "value": "..."} records (HAR, trace params).
  for (const m of matches(
    /"name"\s*:\s*"([^"\\]{1,64})"\s*,\s*"value"\s*:\s*"((?:[^"\\]|\\.)*)"/g,
    text,
  )) {
    const v = unjson(m[2]);
    if (/^(?:set-)?cookie$/i.test(m[1])) {
      for (const pair of v.split(/[;,]\s*/)) {
        const eq = pair.indexOf("=");
        if (eq > 0 && credentialLike(pair.slice(eq + 1)))
          out.push({
            kind: "cookie",
            offset: m.index,
            value: pair.slice(eq + 1),
            name: m[1],
          });
      }
    } else if (credentialName(m[1]) && credentialValue(m[1], v))
      out.push({
        kind: "credential-header",
        offset: m.index,
        value: v,
        name: m[1],
      });
  }
  // Serialized evaluate() arguments: {"k": "<name>", "v": {"s": "<value>"}}.
  for (const m of matches(
    /"k"\s*:\s*"([^"\\]{1,64})"\s*,\s*"v"\s*:\s*\{\s*"s"\s*:\s*"((?:[^"\\]|\\.)*)"/g,
    text,
  ))
    if (credentialName(m[1]) && credentialValue(m[1], unjson(m[2])))
      out.push({
        kind: "credential-field",
        offset: m.index,
        value: unjson(m[2]),
        name: m[1],
      });
  // HAR cookie arrays: every cookie value is a credential in evidence.
  for (const m of matches(
    /"cookies"\s*:\s*\[((?:[^[\]"]|"(?:[^"\\]|\\.)*")*)\]/g,
    text,
  ))
    for (const c of matches(/"value"\s*:\s*"((?:[^"\\]|\\.)*)"/g, m[1]))
      if (credentialLike(unjson(c[1])))
        out.push({
          kind: "cookie",
          offset: m.index + c.index,
          value: unjson(c[1]),
          name: "cookies",
        });
  // URL query/fragment parameters. Percent normalization can expose whitespace
  // inside a list. Read the complete bracketed value instead of classifying a
  // prefix such as `[keywords,`. Overlapping pairs retain the existing value
  // readings without letting a scalar fallback hide a later URL delimiter.
  for (const m of matches(
    /(?=[?&#;]([A-Za-z0-9_.-]{1,64})=(\[[^?&#;]{0,4094}?\](?=$|[?&#;\s"'\\<>])|[^&#\s"'\\<>]{1,4096}))/g,
    text,
  ))
    if (
      (credentialName(m[1]) || QUERY_ONLY.test(m[1])) &&
      credentialValue(m[1], decodeSafe(m[2]))
    )
      out.push({ kind: "url-param", offset: m.index, value: m[2], name: m[1] });
  // Raw header lines and name=value text (logs, error messages); not source code.
  if (code) return;
  for (const m of matches(
    /(?:^|\n|\\n)[ \t]*((?:set-)?cookie|authorization|x-[a-z0-9-]+)[ \t]*:[ \t]*([^\n\\]{6,4096})/gi,
    text,
  )) {
    const name = m[1];
    if (/cookie/i.test(name)) {
      for (const pair of m[2].split(/;\s*/)) {
        const eq = pair.indexOf("=");
        if (eq > 0 && credentialLike(pair.slice(eq + 1)))
          out.push({
            kind: "cookie",
            offset: m.index,
            value: pair.slice(eq + 1),
            name,
          });
      }
    } else if (
      credentialName(name) &&
      credentialValue(name, m[2].replace(/^(?:Bearer|Basic|Token)\s+/i, ""))
    )
      out.push({
        kind: "credential-header",
        offset: m.index,
        value: m[2],
        name,
      });
  }
  // name=value text. Keep quoted items, punctuation and embedded line ends in
  // bracketed values so the list grammar, not a truncated scalar, judges them.
  // An inner closing bracket can end this reading only at a value terminator;
  // any such partial reading still contains unsupported syntax and refuses.
  // Read overlapping pairs: an unrelated outer assignment must not consume a
  // credential pair carried inside its quoted or multiline bracketed value.
  // Serialized line ends are boundaries too, not part of the next field name.
  for (const m of matches(
    /(?=(?:(?<![A-Za-z0-9_.-])|(?<=\\[nrt]))([A-Za-z0-9_.-]{2,64})\s*=\s*(["']?)(\[[\s\S]{0,8190}?\](?=$|[\s"'&;,]|\\[nrt])|[^\s"'&;,]{6,512})\2)/g,
    text,
  ))
    if (credentialName(m[1]) && credentialValue(m[1], m[3]))
      out.push({
        kind: "credential-field",
        offset: m.index,
        value: m[3],
        name: m[1],
      });
}

function decodeSafe(s) {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

const PASSWORDISH =
  /pass(?:word|wd|phrase|code)?|pwd|secret|token|api[-_ ]?key|otp|one[-_ ]?time|credential/i;

function passwordFieldHits(text, out) {
  // DOM snapshots: ["INPUT", {attrs...}] with a password type or autocomplete.
  for (const m of matches(
    /\[\s*"INPUT"\s*,\s*\{((?:[^{}"]|"(?:[^"\\]|\\.)*")*)\}/gi,
    text,
  )) {
    const attrs = m[1];
    const typed =
      /"type"\s*:\s*"password"/i.test(attrs) ||
      /"autocomplete"\s*:\s*"(?:current-password|new-password|one-time-code)"/i.test(
        attrs,
      );
    if (!typed) continue;
    for (const v of matches(
      /"(?:__playwright_value_|value)"\s*:\s*"((?:[^"\\]|\\.)*)"/g,
      attrs,
    ))
      if (unjson(v[1]).length > 0 && !PLACEHOLDER.test(unjson(v[1])))
        out.push({
          kind: "password-field",
          offset: m.index,
          value: unjson(v[1]),
          name: "INPUT[type=password]",
        });
  }
  // Recorded actions: any flat object that targets a password-ish selector and
  // carries a typed value (fill/type/pressSequentially, runner step previews).
  for (const m of matches(/\{((?:[^{}"]|"(?:[^"\\]|\\.)*")*)\}/g, text)) {
    const sel = /"selector"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(m[1]);
    if (!sel || !PASSWORDISH.test(sel[1])) continue;
    for (const v of matches(
      /"(?:value|text)"\s*:\s*"((?:[^"\\]|\\.)*)"/g,
      m[1],
    )) {
      const value = unjson(v[1]);
      if (value.length > 0 && !PLACEHOLDER.test(value) && !MASKED.test(value))
        out.push({
          kind: "password-field",
          offset: m.index,
          value,
          name: "selector",
        });
    }
  }
}

const URL_TOKEN_PATH =
  /\/(?:accept-invitation|invitation\/accept|invitation\/set-password|password-reset-confirm|reset-password|password\/reset|activate|verify|confirm|shared|ingress|magic-link|invite)\/([^/?#\s"'\\<>]+)(?:\/([^/?#\s"'\\<>]+))?/gi;

function urlTokenHits(text, out) {
  for (const m of matches(URL_TOKEN_PATH, text))
    for (const seg of [m[1], m[2]])
      if (
        seg &&
        credentialLike(decodeSafe(seg)) &&
        !/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(seg)
      )
        out.push({
          kind: "url-token",
          offset: m.index,
          value: seg,
          name: "path",
        });
}

function entropyHits(text, out, inspected) {
  const exempt = (index, token) => {
    const before = text.slice(Math.max(0, index - 48), index);
    const after = text.slice(index + token.length, index + token.length + 8);
    if (/[A-Za-z][A-Za-z-]*@$/.test(before)) return true; // Playwright object guid
    if (/(?:sha(?:1|256|384|512)[:-]|resources\/|src@)$/i.test(before))
      return true;
    if (
      /^\.(?:json|html?|css|m?js|png|jpe?g|gif|webp|svg|txt|dat|bin|zip|woff2?|ttf|ico|map|xml|wasm)\b/i.test(
        after,
      )
    )
      return true; // content-addressed file name
    if (/(?:trace|traces|span|spans)[/=]$/i.test(before)) return true; // OTLP ids in paths
    return ID_NAME.test(nameBefore(text, index));
  };
  for (const m of matches(
    /(?<![A-Za-z0-9])([0-9a-f]{32,}|[0-9A-F]{32,})(?![A-Za-z0-9])/g,
    text,
  )) {
    const t = m[1];
    if (!/\d/.test(t) || !/[a-f]/i.test(t) || entropy(t) < 3) continue;
    if (!exempt(m.index, t))
      out.push({
        kind: "entropy-hex",
        offset: m.index,
        value: t,
        name: nameBefore(text, m.index),
      });
  }
  for (const m of matches(
    /(?<![A-Za-z0-9_+/=-])([A-Za-z0-9_-]{32,})(?![A-Za-z0-9_+/=-])/g,
    text,
  )) {
    const t = m[1];
    if (!/\d/.test(t) || !/[a-z]/.test(t) || !/[A-Z]/.test(t)) continue;
    if ((t.match(/[-_]/g) ?? []).length > 3 || entropy(t) < 4) continue;
    // Base64 (or base64url) of text is an encoding, not a token, but ONLY when
    // audit.mjs actually decoded it and scanned the decoded text (`inspected`).
    // Random key material decodes to binary and stays flagged.
    if (inspected?.has(t)) continue;
    if (!exempt(m.index, t))
      out.push({
        kind: "entropy-b64",
        offset: m.index,
        value: t,
        name: nameBefore(text, m.index),
      });
  }
}

function registryHits(text, registry, out) {
  for (const value of registry) {
    const forms = new Set([
      value,
      JSON.stringify(value).slice(1, -1),
      encodeURIComponent(value),
    ]);
    for (const form of forms) {
      for (let i = text.indexOf(form); i >= 0; i = text.indexOf(form, i + 1))
        out.push({
          kind: "minted-value",
          offset: i,
          value,
          name: nameBefore(text, i),
        });
    }
  }
}

/**
 * Findings in one decoded text layer. `opts.code` turns off the detectors that
 * misfire on source code (entropy, free-text pairs); `opts.binary` keeps only
 * the exact-shape and registry detectors (audit.mjs runs every detector over
 * a binary payload's printable spans separately). `opts.inspected`: the
 * base64/base64url tokens (unpadded) whose decoded text audit.mjs scanned.
 */
export function detect(text, opts = {}) {
  const out = [];
  const registry = opts.registry ?? [];
  if (registry.length) registryHits(text, registry, out);
  shapeHits(text, out);
  if (opts.binary) return out;
  pairHits(text, Boolean(opts.code), out);
  passwordFieldHits(text, out);
  urlTokenHits(text, out);
  if (!opts.code) entropyHits(text, out, opts.inspected);
  for (const hit of out) hit.name = safeName(hit.name);
  return out;
}
