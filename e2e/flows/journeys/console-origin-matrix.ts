// J00.L3 dedicated Console-origin listener wall (input 10-31 ~06:03:
// fix/cdn-runtime-config-744; rebound 10-31 to that branch's gateway P1 fix @ 46e39a4686).
// Node built-ins ONLY, so plain `node` (type
// stripping) can run the offline controls below: no e2e/lib, spec, Playwright or
// FA-repo import. The spec maps verdicts onto J00Assertion / ConsoleCoreBlocked;
// this module never decides applicability and never derives the Console origin.
import { createHash } from 'node:crypto';
import { connect } from 'node:net';
import { brotliCompressSync, brotliDecompressSync, deflateRawSync, deflateSync, gunzipSync, gzipSync, inflateRawSync,
  inflateSync } from 'node:zlib';

// PROVENANCE. Every vector and expected outcome in CANON is copied verbatim from
// the canonical vector file of the Console-origin 404 fix (FA repo,
// services/omega-gateway, fix/cdn-runtime-config-744 @ 46e39a4686: reds 8c36da9c34 and b483418dc0,
// green 7a0553f944, test 46e39a4686), saved as
// console-origin.test.mjs@46e39a4686
// (549 lines, file sha256 5203a0dd426b46bf303727409767204f9cb0bf8a91ccb1ec13280426b1415b53, equal to the
// git object 46e39a4686:services/omega-gateway/test/console-origin.test.mjs).
// Source lines: fixture names 37/41/45; document routes 97-102 + accept 104; CSP
// directives 109-117; absentStaticPaths 176-197; malformedStaticPaths 198-226;
// filesOnlyStaticPaths 231-253; rawRequest default accept 153; methods 255; accepts 265;
// SPA routes 313-318; dotted route 345-346; asset routes 355-358; encoded assets 376-379;
// missing assets 385-388; API root 398-399; no-leak routes 424-430; outcomes 106, 123,
// 126, 128-131, 267, 272, 275, 278, 281, 288 (body keys), 291, 292 (requestId), 321, 324,
// 332, 335, 348, 360, 363, 371, 373, 381, 382, 391, 403, 408, 433, 434. Template symbols
// stay as the literal text '${FA}' and '${CSP_REPORT_PATH}'; the `undefined` (373) and
// `null` (131) header expectations are both null ("header absent"); a regex literal
// (123, 292, 335) is copied as its source text.
// REBIND to 46e39a4686 (from 24a61e0424: 497 lines, file sha256 bf72350f1d57..., vectorSha256
// 3dd753e51e81...). The source adds filesOnlyStaticPaths (231-253) and sends it through the SAME
// GET/HEAD x accepts 404 loop as the absent and malformed vectors (255-260). That loop's 404 body
// is now EXACTLY {error, requestId} (282-293: key set 286-290, error 291, requestId
// /^[0-9a-f]{32}$/ 292), so outcomes.notFound gains `keys` and `requestId` and the absent and
// malformed cells carry the full body too. Every other vector and outcome keeps its value; only
// its line moved (the 14 fixture lines 52-65, the 27 filesOnly lines, 1 loop line, 10 more
// 404-body lines).
// CONSOLE_ORIGIN_SOURCE.vectorSha256 = sha256(JSON.stringify(CANON)), keys in the
// declared order, computed 10-31 by an independent extraction that evaluates (node:vm)
// those source line ranges of the saved file, each line checked against its source shape
// first (NOT by hashing this copy). The SAME extractor, run on the 24a61e0424 file at its old
// lines, reproduces all three previous constants (3dd753e5..., 558de7af..., 5f7d7bc5...).
// consoleOriginProvenanceHolds() is the offline self-check of this copy.
//
// FIXTURE FILES ARE REQUEST VECTORS ONLY. The source plants .env, .well-known/probe.txt,
// .vite/manifest.json and assets/nested/x.js in its test build (52-65), so that those spellings
// hit a REAL file. J00 never plants, binds or reads a file. It sends the 20 filesOnlyStaticPaths
// spellings to the deployed Console listener as raw request-targets, and each must be the exact
// 404 whether or not the deployed build carries that file (the hosted build ships
// .vite/manifest.json, 60-61). The planted bytes appear only as negative mutants in the local
// controls. '/omega/console/assets/missing.js' is BOTH missingAssetRoutes[0] (asset-missing, '*/*',
// status only: 385-393) and filesOnlyStaticPaths[14] (the full 404 contract, both accepts: 246,
// 264-293). Two source assertions, so two cells; their '*/*' requests are byte-identical on purpose.
//
// LISTENER_CANON (its own hash, so CANON's binding above is unchanged)
// copies the Console-listener HTTP cases of the SAME file plus the imported
// constant those cases and the CSP assertions use:
// - cspReportPath: `export const CSP_REPORT_PATH = "/__omega/csp-report";` at
//   services/omega-gateway/src/console-origin.mjs:37 @ 46e39a4686 (git object; file
//   sha256 5a80ae20e02048549eb25fa3f75ebed2d1fb9a065049f53293852eea9c1f9b9a; line 37 unchanged
//   from 24a61e0424, sha256 5e22ce1c3aa5...). The
//   source asserts report-uri against this imported constant (116, 126), so J00
//   compares against it too: never against a value read from the response.
// - apiCases 445-495: routes 448/460/471/482/490; authorization 449/461/474/483/491
//   kept as RAW source text (template expressions are not evaluated); origin
//   461/475/483 (449 and 491 send none); outcomes 451-453, 464, 479-480, 487, 493.
// - cspReports 525-549: route 527/544; method 528/545; content-type 529; body
//   530-536 evaluated with CONSOLE as the literal '${CONSOLE}'; 546 `"x".repeat(20_000)`
//   as {repeat, count}; outcomes 538, 548.
// fetch() serialization applied by J00 (not canonical fields): no method option at
// 445-495 => GET; default Accept '*/*' (CANON.rawDefaultAccept, as for 431); a string
// body with no content-type (545-546) => 'Content-Type: text/plain;charset=UTF-8'
// (Fetch standard, "extract a body"). CONSOLE (17) binds to binding.url: in the source
// the listener base and the configured Console origin are one value.
// CONSOLE_ORIGIN_SOURCE.listenerVectorSha256 = sha256(JSON.stringify(LISTENER_CANON)),
// computed 10-31 by a SECOND independent extraction of those source lines and of
// module line 37 (NOT by hashing this copy); consoleOriginListenerProvenanceHolds().
// Rebind 46e39a4686: re-extracted at the shifted lines (24a61e0424 + 52), byte-identical, so
// the hash is unchanged.
//
// APP_LISTENER_CANON (its own hash, so CANON and LISTENER_CANON are unchanged)
// copies EVERY HTTP outcome of the app-listener test, 497-523 of the same file:
// - api 499-503: route 499, authorization 500 (raw source text: the SAME literal
//   'Bearer fi-browser-token' as 449, so it binds to the same browserToken slot), origin
//   500 (`FA`), outcomes 502 (200) and 503 (access-control-allow-origin === FA).
// - static 506-522: methods 506, routes 508-514, accept 517 (rawRequest, so the raw path),
//   outcome 518 (404).
// fetch() serialization as for 445-495: no method option at 499-501 => GET; default
// Accept '*/*'. FA (16) binds to the J00 public origin, for the Origin header AND the
// expected Access-Control-Allow-Origin. The source sends both groups to `base` (498), the
// app listener's own server, NOT `consoleBase` (446): J00 sends them to
// binding.appListenerUrl, the app listener's OWN origin (not the edge in front of it),
// which must differ from binding.url. It is never derived: services/omega-gateway/src/
// server.mjs:19-21 @46e39a4686 (unchanged since 24a61e0424) puts the app listener "on the Future AGI origin (`/omega/api`)"
// behind the edge (and :62-65 echo an allowed Origin as Access-Control-Allow-Origin), but the J00 L3.01
// wall (default-off-census.spec.ts publicWall) requires /omega/api/workspace/capabilities
// at runtime.origins.public to answer 404 for anonymous/A/B, and 508-514 probe
// non-/omega/api paths that an edge in front of the FA origin routes to the frontend. So
// runtime.origins.public is not assumed to be the listener. Absent/malformed => every
// app-listener cell is BLOCKED value-free (J00_CONSOLE_ORIGIN_APP_LISTENER; an https
// origin => J00_CONSOLE_ORIGIN_APP_LISTENER_RAW_TRANSPORT); no browserToken => the api
// cell is BLOCKED (J00_CONSOLE_ORIGIN_CREDENTIAL_BROWSER_TOKEN) and nothing is sent.
// CONSOLE_ORIGIN_SOURCE.appListenerVectorSha256 = sha256(JSON.stringify(APP_LISTENER_CANON)),
// computed 10-31 by a THIRD independent extraction that evaluates source lines 497-523
// (NOT by hashing this copy); consoleOriginAppListenerProvenanceHolds(). Rebind 46e39a4686:
// re-extracted at the shifted lines, byte-identical, so the hash is unchanged.
// Other gateway files cited below (server.mjs, lifecycle.mjs, trace.mjs, credential.mjs,
// assertion.mjs) and frontend/src/sections/omega/console/e2e/README.md are byte-identical at
// 24a61e0424 and 46e39a4686: under services/omega-gateway, 24a61e0424..46e39a4686 changes only
// src/console-origin.mjs and test/console-origin.test.mjs.
//
// Response CONTENT. Every raw request carries 'Accept-Encoding: identity' (a J00
// serialization: rawRequest 153-174 sends none and node:http never decodes; fetch would
// advertise and decode codings). Every body predicate reads responseContent(): the
// framing-decoded body after gzip/x-gzip/deflate/br content decoding (node:zlib, at most
// CONTENT_DECODE_LIMIT bytes), or null (undecodable/unknown coding, over the limit, or a
// transfer coding other than chunked) so the predicate fails closed and no-leak is never
// cleared on bytes it could not read. Any non-identity coding is ALSO the contract failure
// L3.09-content-encoding: J00 asked for identity, and the canonical rawRequest cases compare
// raw bytes (285-292, 338, 349, 374, 383), so a coded answer fails them canonically.
//
// CSP. The enforced policy is checked twice: the canonical literal check
// (csp.split("; ").includes(directive), 119) AND a CSP3 parse (W3C CSP3, "parse a serialized
// CSP"): split on every ';', strip ASCII whitespace, skip empty and non-ASCII tokens,
// lowercase the directive name, split the value on ASCII whitespace. J00-authored strictness,
// beyond the test file: no ',' (a second enforced policy), every canonical directive name
// present EXACTLY once with exactly the canonical value (CSP3 would let a first duplicate
// win), so exactly one report-uri equal to CSP_REPORT_PATH; and NO report-to directive (the
// canonical module emits none, console-origin.mjs:45-57, and CSP3 user agents that honour
// report-to ignore report-uri, so it would move the reporting destination).
//
// Deliberately NOT copied as J00 assertions (recorded, never silently dropped):
// - 297-298 calls.exchange/upstream: gateway-internal harness counters, not HTTP-observable.
// - 413-420 consolePolicies and 436-442 resolveStaticFile: pure in-process unit calls, no
//   HTTP vector.
// - 454-458 calls.exchange, 465 and 494 calls.upstream counts, 466-469 the authorization
//   header captured by the stub upstream: in-process harness counters/captures. The HTTP
//   status, error body and header outcomes of those same requests ARE copied.
// - 504 calls.exchange.length === 1: in-process counter of the harness's session-exchange
//   stub. The 200 and Access-Control-Allow-Origin of that same request (502-503) and every
//   static 404 (518) ARE copied.
// - 539-543 CSP report log line (reason; no 'secret-agent' or 'token=abc'): gateway log
//   contents, not HTTP-observable. The two 204 statuses (538, 548) ARE copied.
// - The planted fixture FILES 52-65 (.env 'NOT_A_SECRET=fixture', .well-known/probe.txt,
//   .vite/manifest.json, assets/nested/x.js): never planted or bound (see FIXTURE FILES); their
//   spellings ARE copied, as the filesOnlyStaticPaths request vectors.
// - Test-BUILD bytes (`<title>Console</title>` 107, 'console.log(1)' 145/374,
//   'encoded asset' 383, index.html 309-312, the leak.txt symlink 66-70): replaced by
//   the deployed image's document/asset byteLength+sha256 from the Stage A binding. A bound
//   file must be NON-EMPTY (byteLength > 0, not the empty digest; else BLOCKED value-free
//   under J00_CONSOLE_ORIGIN_DOCUMENT_EMPTY / J00_CONSOLE_ORIGIN_ASSET_*_EMPTY), and every
//   positive SPA/document/asset body must be non-empty AND equal to the bound bytes.
//   The document body (107) is checked on ALL four document routes against the bound
//   root document, /omega/console/console.html included: the fixture writes the same
//   bytes to index.html and console.html (32-35), and the 46e39a4686 harness layout
//   copies console.html to the root index.html (frontend/src/sections/omega/console/
//   e2e/README.md:76-82). A deployed console.html that differs needs a ruling.
//   FA (16, 'http://localhost:3031') binds to the J00 public origin (the frame parent).
// - Credentials (449 and 500 literal 'fi-browser-token'; 459/474/483/491 mintAssertion with
//   the test JWKS): J00 sends ONLY binding.credentials values, one distinct pre-minted
//   assertion per case because the source mints a fresh one per request and EE
//   consumes jti/nonce once (src/assertion.mjs:7). Absent/malformed => that cell is
//   BLOCKED value-free and nothing is sent. No value reaches a cell, verdict or receipt.
export const CONSOLE_ORIGIN_SOURCE = Object.freeze({
  ref: 'console-origin.test.mjs@46e39a4686',
  branch: 'fix/cdn-runtime-config-744', commit: '46e39a4686', red: ['8c36da9c34', 'b483418dc0'], green: '7a0553f944', lines: 549,
  fileSha256: 'sha256:5203a0dd426b46bf303727409767204f9cb0bf8a91ccb1ec13280426b1415b53',
  vectorSha256: 'sha256:9742b3a9efa36ab39563771e66f79cc96dd0adacb4629eabaf4d986d9c310d36',
  module: 'services/omega-gateway/src/console-origin.mjs@46e39a4686',
  moduleSha256: 'sha256:5a80ae20e02048549eb25fa3f75ebed2d1fb9a065049f53293852eea9c1f9b9a',
  listenerVectorSha256: 'sha256:558de7aff43a8662f89409f31ad9fab321aaa0d0806bea623c471ffbea46a61f',
  appListenerVectorSha256: 'sha256:5f7d7bc51ae845d20b7608abb80b73f8ef2a1187da94815e3360621a81648239',
});

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

const CANON = deepFreeze({
  fixtureAssetNames: ['app-Ab12Cd34.js', 'naïve plan.txt', '100% ready.txt'],
  documentHeaderRoutes: ['/omega/console', '/omega/console/', '/omega/console/agents/agent-1', '/omega/console/console.html'],
  documentAccept: 'text/html',
  documentCspDirectives: ["default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'none'",
    'frame-ancestors ${FA}', "connect-src 'self'", 'report-uri ${CSP_REPORT_PATH}'],
  absentStaticPaths: [
    '/',
    '/omega',
    '/omega/',
    '/omega/foo',
    '/omega/foo/nested',
    '/agents/agent-1',
    '/omega/consolex',
    '/omega/consolex/agents',
    '/omega/console-old',
    '/omega/console.html',
    '/omega/api-ish',
    '/omega/apix',
    '/omega/apix/workspace/capabilities',
    '/omega/api.json',
    '/omega/api%2fworkspace/capabilities',
    '/omega/%61pi/workspace/capabilities',
    '/index.html',
    '/robots.txt',
    '/assets/missing.js',
    '/omega/foo?view=agents',
  ],
  malformedStaticPaths: [
    '/omega/console/.',
    '/omega/console/..',
    '/omega/console/../foo',
    '/omega/console/../../index.html',
    '/omega/console/agents/../console.html',
    '/omega/console/./agents',
    '/omega/./console/agents',
    '/omega/foo/../console/agents',
    '/omega/console/%2e/agents',
    '/omega/console/%2e%2e/foo',
    '/omega/console/.%2E/foo',
    '/omega/console/%2e./foo',
    '/omega/console/assets/..%2f..%2findex.html',
    '/omega/console/%2F..%2F..%2Findex.html',
    '/omega/console/assets%5c..%5cindex.html',
    '/omega/console/agents\\..\\console.html',
    '/omega/console/%252e%252e%252ffoo',
    '/omega/console/%25252e%25252e%25252ffoo',
    '/omega/console%2fagents',
    '/omega%2fconsole/agents',
    '/omega/%63onsole/agents',
    '/omega/console/%00',
    '/omega/console/%E0%A4%A',
    '/omega/console/%',
    '//omega/console/agents',
    '/omega//console/agents',
    '/omega/console//agents',
  ],
  filesOnlyStaticPaths: [
    '/omega/console/.env',
    '/omega/console/.env.local',
    '/omega/console/.git/config',
    '/omega/console/.well-known/probe.txt',
    '/omega/console/.well-known/',
    '/omega/console/.vite/manifest.json',
    '/omega/console/agents/.hidden',
    '/omega/console/assets/.env',
    '/omega/console/%2Eenv',
    '/omega/console/assets',
    '/omega/console/assets/',
    '/omega/console/assets/nested',
    '/omega/console/assets/nested/',
    '/omega/console/assets/no-extension',
    '/omega/console/assets/missing.js',
    '/omega/console/%61ssets',
    '/omega/console/%61ssets/',
    '/omega/console/%61ssets/nested/',
    '/omega/console/assets/%2Eenv',
    '/omega/console/%2E%67it/config',
  ],
  rawDefaultAccept: '*/*',
  methods: ['GET', 'HEAD'],
  accepts: ['*/*', 'text/html'],
  spaRoutes: ['/omega/console', '/omega/console/', '/omega/console/agents/agent-1', '/omega/console/agents/agent-1/runs?view=recent'],
  dottedSpaRoute: '/omega/console/agents/agent.v2',
  dottedAccept: 'text/html',
  assetRoutes: ['/omega/console/assets/app-Ab12Cd34.js', '/omega/console/assets/%61pp-Ab12Cd34%2Ejs'],
  encodedAssetRoutes: ['/omega/console/assets/na%C3%AFve%20plan.txt', '/omega/console/assets/100%25%20ready.txt'],
  missingAssetRoutes: ['/omega/console/assets/missing.js', '/omega/console/leak.txt'],
  apiRootRoute: '/omega/api',
  apiRootAccept: 'text/html',
  noLeakRoutes: ['/../../etc/passwd', '/%2e%2e/%2e%2e/etc/passwd', '/omega/console/leak.txt',
    '/omega/console/assets/..%2f..%2fleak.txt', '/%00'],
  outcomes: {
    notFound: { status: 404, contentType: 'application/json; charset=utf-8', cacheControl: 'no-store',
      contentTypeOptions: 'nosniff', headBody: '', error: 'not_found', keys: ['error', 'requestId'], requestId: '^[0-9a-f]{32}$' },
    spa: { status: 200, contentType: 'text/html; charset=utf-8', cacheControl: 'no-cache', csp: "default-src 'self'" },
    documentHeaders: { status: 200, forbiddenCsp: 'unsafe-inline|unsafe-eval|\\*',
      reportOnly: "require-trusted-types-for 'script'; report-uri ${CSP_REPORT_PATH}", contentTypeOptions: 'nosniff',
      referrerPolicy: 'no-referrer', cacheControl: 'no-cache', allowOrigin: null },
    dotted: { status: 200 },
    asset: { status: 200, contentType: 'text/javascript; charset=utf-8', cacheControl: 'public, max-age=31536000, immutable', csp: null },
    encodedAsset: { status: 200, cacheControl: 'no-cache' },
    missingAsset: { status: 404 },
    apiRoot: { status: 403, contentType: 'application/json; charset=utf-8' },
    noLeak: { forbidden: ['outside the build', 'root:'] },
  },
});
export type ConsoleOriginCanon = typeof CANON;

export interface ConsoleOriginApiCase {
  id: string; method: string; path: string; authorization: string; origin: string | null;
  status: number; error?: string; contentTypeOptions?: string;
}
export interface ConsoleOriginCspReportCase {
  id: string; method: string; path: string; contentType: string | null;
  body: string | { repeat: string; count: number }; status: number;
}
export interface ConsoleOriginListenerCanon {
  cspReportPath: string; apiCases: readonly ConsoleOriginApiCase[]; cspReports: readonly ConsoleOriginCspReportCase[];
}
const LISTENER_CANON = deepFreeze<ConsoleOriginListenerCanon>({
  cspReportPath: '/__omega/csp-report',
  apiCases: [
    { id: 'browser-token', method: 'GET', path: '/omega/api/workspace/capabilities', authorization: 'Bearer fi-browser-token',
      origin: null, status: 401, error: 'omega_assertion_required', contentTypeOptions: 'nosniff' },
    { id: 'assertion', method: 'GET', path: '/omega/api/workspace/capabilities', authorization: 'Bearer ${assertion}',
      origin: '${CONSOLE}', status: 200 },
    { id: 'foreign-origin', method: 'GET', path: '/omega/api/workspace/capabilities', authorization: 'Bearer ${mintAssertion()}',
      origin: 'https://evil.example', status: 403, error: 'origin_not_allowed' },
    { id: 'fa-origin', method: 'GET', path: '/omega/api/workspace/capabilities', authorization: 'Bearer ${mintAssertion()}',
      origin: '${FA}', status: 403 },
    { id: 'operator', method: 'GET', path: '/omega/api/os/agents', authorization: 'Bearer ${mintAssertion({ role: "owner" })}',
      origin: null, status: 403 },
  ],
  cspReports: [
    { id: 'csp-report', method: 'POST', path: '${CSP_REPORT_PATH}', contentType: 'application/csp-report',
      body: '{"csp-report":{"violated-directive":"script-src-elem","blocked-uri":"https://evil.example/x.js?token=abc","document-uri":"${CONSOLE}/agents/secret-agent"}}',
      status: 204 },
    { id: 'csp-oversized', method: 'POST', path: '${CSP_REPORT_PATH}', contentType: null, body: { repeat: 'x', count: 20000 }, status: 204 },
  ],
});
/** fetch() default for a string body sent without a content-type (545-546). */
const FETCH_STRING_BODY_TYPE = 'text/plain;charset=UTF-8';

export interface ConsoleOriginAppListenerCanon {
  api: { id: string; method: string; path: string; authorization: string; origin: string; status: number; allowOrigin: string };
  staticMethods: readonly string[]; staticRoutes: readonly string[]; staticAccept: string; staticStatus: number;
}
// Source 497-523 (see the header): key order is the extraction's, so the hash binds it.
const APP_LISTENER_CANON = deepFreeze<ConsoleOriginAppListenerCanon>({
  api: { id: 'sandboxes', method: 'GET', path: '/omega/api/workspace/sandboxes', authorization: 'Bearer fi-browser-token',
    origin: '${FA}', status: 200, allowOrigin: '${FA}' },
  staticMethods: ['GET', 'HEAD'],
  staticRoutes: ['/', '/omega', '/omega/foo', '/omega/console', '/omega/console/agents/agent-1',
    '/omega/console/assets/app-Ab12Cd34.js', '/index.html'],
  staticAccept: 'text/html',
  staticStatus: 404,
});

const sha256 = (bytes: string | Buffer) => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
/** Offline self-check: the copied arrays hash to the constant computed from the source file. */
export function consoleOriginProvenanceHolds(canon: ConsoleOriginCanon = CANON): boolean {
  return sha256(JSON.stringify(canon)) === CONSOLE_ORIGIN_SOURCE.vectorSha256;
}
/** Offline self-check of the listener copy against its independently extracted hash. */
export function consoleOriginListenerProvenanceHolds(listener: ConsoleOriginListenerCanon = LISTENER_CANON): boolean {
  return sha256(JSON.stringify(listener)) === CONSOLE_ORIGIN_SOURCE.listenerVectorSha256;
}
/** Offline self-check of the app-listener copy against its independently extracted hash. */
export function consoleOriginAppListenerProvenanceHolds(app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON): boolean {
  return sha256(JSON.stringify(app)) === CONSOLE_ORIGIN_SOURCE.appListenerVectorSha256;
}

// NOT canonical: J00-authored spellings the source does not cover (the Console listener
// answered 200/204 to some). RULINGS (10-31) fix
// each one HERE, in code: no binding input can re-rule a cell (`rulings` is no longer read). Every
// cell is sent raw and gets the checks every sent cell gets (transport, HEAD no body, identity
// content: a coded body is unreadable, so a body predicate fails closed).
// - 'status-only' (ruling b; healthz, readyz, GET+HEAD): ALLOW, the status is recorded (receipt
//   'recorded' + status), PLUS the GET body carries only a status (statusOnlyBody: an ALLOWLIST over
//   the raw bytes, no repeated member, keys status/ok/live/ready/checks, and requestId only at >= 400);
//   violation fails. Interpretation (labelled at statusOnlyBody): requestId, the per-request trace id,
//   is not build information.
// - 'no-credentialed-cors' (ruling c; OPTIONS / and /omega/console/): ALLOW, status recorded, PLUS
//   no 'Access-Control-Allow-Origin: *' or 'null' with 'Access-Control-Allow-Credentials: true' and no
//   reflection of an arbitrary Origin with credentials; violation fails. J00 serialization: the
//   request carries PREFLIGHT_ORIGIN (the source's own arbitrary foreign origin, 461/475) and
//   'Access-Control-Request-Method: GET', so a reflection is observable at all.
// - 'exact-404' (ruling d): the not-found status, headers and HEAD body of outcomes.notFound (404,
//   JSON content type, no-store, nosniff; HEAD empty; notFoundShapeHolds, the piece shared with the
//   canonical contract, NOT the 46e39a4686 full-body rule) AND exactly the gateway's body,
//   {"error":"not_found"} with at most its requestId (ruledNotFoundHolds: no other member, none
//   twice); GET and HEAD agree; so never a file body or a directory listing. Unchanged by the rebind.
// - '404-or-spa' (ruling e): that exact 404, OR 200 whose GET body equals the bound non-empty document
//   (SPA fallback by design; HEAD no body, Content-Length = its byteLength); anything else (other
//   bytes, a file, another status) fails; GET and HEAD agree. The 200 branch imposes nothing beyond
//   the ruling: the canonical SPA header names it does not carry are RECORDED in the receipt
//   (nonCanonicalHeaders), never failed.
// ONE CELL PER SPELLING (rebind 46e39a4686). Two ruled spellings are now ALSO canonical vectors:
// 'dotfile' ('/omega/console/.env' = filesOnlyStaticPaths[0]) and 'assets-directory'
// ('/omega/console/assets/' = filesOnlyStaticPaths[10]), both 'exact-404', both GET+HEAD x both
// accepts. Each is planned ONCE, as its canonical cell (files-only#0|..., files-only#10|...; the
// former ruled#dotfile|... and ruled#assets-directory|... ids are no longer planned). That cell
// carries the ruling (ruledId/ruling) and runs BOTH predicates, the canonical full-body 404
// (L3.09-not-found-*) AND ruling d (L3.09-ruled-404-*), so it holds the STRICTER expectation: the
// canonical rule demands the requestId that ruling d leaves optional, and ruling d's strict members
// were already part of it. CONSOLE_ORIGIN_RULED itself is unchanged. Any other overlap between a
// ruled spelling and a canonical static vector needs a ruling: L3.09-local-ruled-canonical-overlap
// pins exactly this set.
// GATEWAY (ruling d vs services/omega-gateway/src/console-origin.mjs). At 24a61e0424 the dotfile and
// assets-directory spellings got the SPA fallback (200, index.html): isConsoleStaticPath accepted both,
// resolveStaticFile found no regular file, and wantsDocument held because path.extname is ''. At
// 46e39a4686 (green 7a0553f944) isConsoleStaticPath (120-143) refuses any decoded segment starting with
// '.' (141), so '/omega/console/.env' is not a Console static path. '/omega/console/assets/' still is
// one, resolveStaticFile finds no regular file (109), and isFilesOnlyPath (148-152) bars the fallback
// at or below /omega/console/assets (177). So serveStatic returns false for both (173, 179) and the
// request reaches `respond(404, { error: "not_found" })` (server.mjs:266), whose body is
// {"error":"not_found","requestId":"<32 hex>"} (server.mjs:201), as the source's full-body rule
// requires (286-292). An image built before 7a0553f944 fails those cells live. 7a0553f944 only
// narrows isConsoleStaticPath and the fallback, so the other four exact-404 targets and both
// upper-case targets stay non-static and reach the same 404. Ruling b: a not-ready or
// draining readyz (503, lifecycle.mjs:52, 60) carries the same requestId and holds under the
// requestId interpretation; any other member would fail.
export type ConsoleOriginMethod = 'GET' | 'HEAD' | 'OPTIONS' | 'POST';
export type ConsoleOriginRuling = 'status-only' | 'no-credentialed-cors' | 'exact-404' | '404-or-spa';
type RuledCell = { id: string; target: string; methods: readonly ConsoleOriginMethod[]; ruling: ConsoleOriginRuling };
export const CONSOLE_ORIGIN_RULED = deepFreeze<readonly RuledCell[]>([
  { id: 'healthz', target: '/healthz', methods: ['GET', 'HEAD'], ruling: 'status-only' },
  { id: 'readyz', target: '/readyz', methods: ['GET', 'HEAD'], ruling: 'status-only' },
  { id: 'options-root', target: '/', methods: ['OPTIONS'], ruling: 'no-credentialed-cors' },
  { id: 'options-console', target: '/omega/console/', methods: ['OPTIONS'], ruling: 'no-credentialed-cors' },
  { id: 'upper-case-path', target: '/OMEGA/CONSOLE/agents', methods: ['GET', 'HEAD'], ruling: '404-or-spa' },
  { id: 'upper-case-console', target: '/omega/CONSOLE', methods: ['GET', 'HEAD'], ruling: '404-or-spa' },
  { id: 'path-parameter', target: '/omega/console;x=1', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
  { id: 'encoded-trailing-slash', target: '/omega/console%2F', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
  { id: 'upper-encoded-dot-dot', target: '/omega/console/%2E%2E/foo', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
  { id: 'backslash-separator', target: '/omega\\console/agents', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
  { id: 'dotfile', target: '/omega/console/.env', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
  { id: 'assets-directory', target: '/omega/console/assets/', methods: ['GET', 'HEAD'], ruling: 'exact-404' },
]);
/** Rulings b and c: ALLOW with the status recorded (their own predicates can still fail the cell). */
const RECORDED_RULINGS: readonly ConsoleOriginRuling[] = ['status-only', 'no-credentialed-cors'];
/** Ruling c request: the source's arbitrary foreign origin (461/475, LISTENER_CANON 'foreign-origin'). */
export const PREFLIGHT_ORIGIN = 'https://evil.example';
const PREFLIGHT_METHOD = 'GET';

export type ConsoleOriginAssetSlot = 'immutable-js' | 'utf8-space-name' | 'percent-space-name';
export interface ConsoleOriginFile { byteLength: number; sha256: string }
export interface ConsoleOriginAsset extends ConsoleOriginFile { name: string }
export type ConsoleOriginAssertionCase = 'assertion' | 'foreign-origin' | 'fa-origin' | 'operator';
/**
 * Producer-minted, in-memory only; never logged, hashed into a receipt or put in a cell.
 * `browserToken` is a Future AGI browser session token (the source's 'fi-browser-token',
 * 449): it must NOT be an omega_delegate_ assertion. `assertions` holds one distinct,
 * unexpired (<= 45 s) pre-minted omega_delegate_ assertion per case, each the gateway
 * accepts for the Console origin; 'operator' must carry role owner (491). J00 checks the
 * form only (no JWT decoding here); claims are the producer's obligation.
 */
export interface ConsoleOriginCredentials {
  browserToken?: string;
  assertions?: Partial<Record<ConsoleOriginAssertionCase, string>>;
}
/**
 * Stage A/composition-bound input, NOT derived by J00. `url` is the exact Console
 * listener origin; `document` is the deployed image's root fallback index.html (non-empty);
 * `assets` maps the immutable-js fixture slot to a deployed-image file name under
 * omega/console/assets/ (decoded; ruling a: the other two canonical slots are not applicable
 * to deployed builds and are never read); `credentials` carries the listener API cases'
 * bearer values; `appListenerUrl` is the
 * exact http origin of the gateway's app listener ITSELF (source 498 `base`; not the edge
 * or frontend in front of it, and never the Console listener `url`).
 */
export interface ConsoleOriginBinding {
  url: string;
  appListenerUrl?: string;
  document?: ConsoleOriginFile;
  assets?: { 'immutable-js'?: ConsoleOriginAsset };
  credentials?: ConsoleOriginCredentials;
}

type Kind = 'absent' | 'malformed' | 'files-only' | 'spa' | 'spa-dotted' | 'document' | 'asset' | 'asset-encoded'
  | 'asset-missing' | 'api-root' | 'no-leak' | 'api' | 'csp-report' | 'app-api' | 'app-absent' | 'ruled';
const APP_KINDS: readonly Kind[] = ['app-api', 'app-absent'];
export interface ConsoleOriginCell {
  id: string; kind: Kind; method: ConsoleOriginMethod; accept: string;
  /** null => not sent (value-free BLOCKED, or not applicable); otherwise the literal request-target bytes. */
  target: string | null;
  /** Binding seam that caps this cell at BLOCKED (structural checks still run when sent). */
  gap: string | null;
  pair: string; slot: ConsoleOriginAssetSlot | null;
  /** CONSOLE_ORIGIN_RULED id and ruling for 'ruled' cells, and for a canonical cell that is also a ruled spelling (mergedRuling). */
  ruledId: string | null; ruling: ConsoleOriginRuling | null;
  /** Ruling a: planned, never sent, receipted 'not_applicable', never BLOCKED. */
  notApplicable: boolean;
  /** LISTENER_CANON case id for 'api' / 'csp-report' cells (never a credential value). */
  caseId: string | null;
}
export interface ConsoleOriginObservation {
  cell: string;
  /** Exact bytes handed to the socket's single write. */
  written: Buffer;
  status: number; headers: Readonly<Record<string, string>>;
  /** Framing-decoded body; always empty for HEAD (raw bytes are counted separately). */
  body: Buffer;
  /** Raw bytes received after the FINAL header block (1xx skipped); -1 when none parsed. */
  bytesAfterHeaders: number;
  transportError: boolean;
}
export interface ConsoleOriginCellReceipt {
  cell: string; verdict: 'pass' | 'fail' | 'blocked' | 'recorded' | 'not_applicable'; status?: number;
  /** Ruling e's 200 branch only: the canonical SPA header NAMES (outcomes.spa) it did not carry. Recorded, never failed; never a value. */
  nonCanonicalHeaders?: string[];
}
export interface ConsoleOriginVerdict { passed: boolean; failures: string[]; blocked: string[]; cells: ConsoleOriginCellReceipt[] }
/** What the runner and the evaluator both need to rebuild the exact request bytes. */
export interface ConsoleOriginRunInput { binding: unknown; publicOrigin: string }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);
/** The bound Console listener origin, or null (caller refuses BLOCKED). Never a fallback. */
export function consoleOriginBindingUrl(binding: unknown): string | null {
  if (!isRecord(binding) || typeof binding.url !== 'string') return null;
  try {
    const url = new URL(binding.url);
    return ['http:', 'https:'].includes(url.protocol) && url.origin === binding.url && !url.username && !url.password
      ? binding.url : null;
  } catch { return null; }
}
export const APP_LISTENER_GAP = 'J00_CONSOLE_ORIGIN_APP_LISTENER';
export const APP_LISTENER_TRANSPORT_GAP = 'J00_CONSOLE_ORIGIN_APP_LISTENER_RAW_TRANSPORT';
/**
 * The bound app-listener origin (source 498 `base`), or the seam that caps its cells at
 * BLOCKED. Exact http origin only (raw transport), distinct from the Console listener.
 */
export function consoleOriginAppListener(binding: unknown): { url: string | null; gap: string | null } {
  const value = isRecord(binding) ? binding.appListenerUrl : undefined;
  const url = consoleOriginBindingUrl({ url: value });
  if (!url || url === consoleOriginBindingUrl(binding)) return { url: null, gap: APP_LISTENER_GAP };
  return new URL(url).protocol === 'http:' ? { url, gap: null } : { url: null, gap: APP_LISTENER_TRANSPORT_GAP };
}
/** Which listener a cell is sent to: app-listener kinds to appListenerUrl, every other kind to url. */
export function consoleOriginCellOrigin(cell: Pick<ConsoleOriginCell, 'kind'>, binding: unknown): string | null {
  return APP_KINDS.includes(cell.kind) ? consoleOriginAppListener(binding).url : consoleOriginBindingUrl(binding);
}
function readFile(value: unknown): ConsoleOriginFile | null {
  return isRecord(value) && Number.isSafeInteger(value.byteLength) && (value.byteLength as number) >= 0
    && typeof value.sha256 === 'string' && /^sha256:[a-f0-9]{64}$/.test(value.sha256)
    ? { byteLength: value.byteLength as number, sha256: value.sha256 } : null;
}
/** sha256 of zero bytes: a bound file with this digest is empty, whatever byteLength it claims. */
const EMPTY_SHA256 = sha256(Buffer.alloc(0));
/**
 * A bound deployed file the positive cells may compare against: well-formed AND non-empty
 * (byteLength > 0 and not the empty digest). An empty Console document or asset never
 * qualifies the wall: `empty` lets the caller refuse it BLOCKED
 * under its own seam, value-free, instead of matching empty answers.
 */
function readBoundFile(value: unknown): { file: ConsoleOriginFile | null; empty: boolean } {
  const file = readFile(value);
  if (!file) return { file: null, empty: false };
  return file.byteLength === 0 || file.sha256 === EMPTY_SHA256 ? { file: null, empty: true } : { file, empty: false };
}
export const DOCUMENT_GAP = 'J00_CONSOLE_ORIGIN_DOCUMENT';
export const DOCUMENT_EMPTY_GAP = 'J00_CONSOLE_ORIGIN_DOCUMENT_EMPTY';
function documentGapOf(value: unknown): string | null {
  const bound = readBoundFile(value);
  return bound.file ? null : bound.empty ? DOCUMENT_EMPTY_GAP : DOCUMENT_GAP;
}
const safeName = (name: string) => name.length > 0 && name !== '.' && name !== '..' && !/[/\\\u0000-\u001f\u007f]/.test(name)
  && (() => { try { encodeURIComponent(name); return true; } catch { return false; } })();
const ASSET_PREFIX = '/omega/console/assets/';
/** Canonical form of assetRoutes[1]: first character as lower-case %XX, last '.' as %2E. */
function unreservedEncoded(name: string): string {
  const dot = name.lastIndexOf('.');
  return `%${name.charCodeAt(0).toString(16)}${name.slice(1, dot)}%2E${name.slice(dot + 1)}`;
}
// RULING a (10-31): the utf8-space-name and percent-space-name
// slots are NOT APPLICABLE to deployed builds (Vite emits hashed ASCII asset names). Their decoding
// is covered by the edge's own unit tests, services/omega-gateway/test/console-origin.test.mjs:376-383
// @46e39a4686 (fixtures :41, :45). Their cells (asset-encoded#0/#1) stay planned but are never sent
// (an answer for one is L3.09-unplanned-cell), never read from the binding, receipted
// 'not_applicable' and never BLOCK the L3 leg; their forms still reproduce the canonical vectors
// (consoleOriginAssetFormsHold, provenance). The unreserved-encoded immutable-js cell (asset#1,
// 355-358) stays.
const ASSET_SLOTS = [
  { slot: 'immutable-js', applicable: true, gap: 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS',
    emptyGap: 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS_EMPTY', kind: 'asset',
    valid: (name: string) => /^[A-Za-z][A-Za-z0-9._~-]*\.js$/.test(name) && !name.includes('..'),
    targets: (name: string) => [ASSET_PREFIX + name, ASSET_PREFIX + unreservedEncoded(name)] },
  { slot: 'utf8-space-name', applicable: false, kind: 'asset-encoded',
    valid: (name: string) => safeName(name) && /[^\u0000-\u007f]/.test(name) && name.includes(' '),
    targets: (name: string) => [ASSET_PREFIX + encodeURIComponent(name)] },
  { slot: 'percent-space-name', applicable: false, kind: 'asset-encoded',
    valid: (name: string) => safeName(name) && name.includes('%') && name.includes(' '),
    targets: (name: string) => [ASSET_PREFIX + encodeURIComponent(name)] },
] as const;
/** A valid, non-empty bound asset; `empty` marks a well-formed binding of an empty file (refused, own seam). */
function readAsset(value: unknown, valid: (name: string) => boolean): { asset: ConsoleOriginAsset | null; empty: boolean } {
  if (!isRecord(value) || typeof value.name !== 'string' || !valid(value.name)) return { asset: null, empty: false };
  const { file, empty } = readBoundFile(value);
  return { asset: file ? { ...file, name: value.name } : null, empty };
}
/** The J00 asset forms reproduce the canonical asset vectors from the canonical fixture names. */
export function consoleOriginAssetFormsHold(canon: ConsoleOriginCanon = CANON): boolean {
  const names = canon.fixtureAssetNames;
  if (names.length !== 3 || !names.every(name => typeof name === 'string')) return false;
  const [js, utf8, percent] = names;
  const [immutable, utf8Slot, percentSlot] = ASSET_SLOTS;
  return immutable.valid(js) && utf8Slot.valid(utf8) && percentSlot.valid(percent)
    && JSON.stringify(immutable.targets(js)) === JSON.stringify(canon.assetRoutes)
    && JSON.stringify([...utf8Slot.targets(utf8), ...percentSlot.targets(percent)]) === JSON.stringify(canon.encodedAssetRoutes);
}
// J00 mapping of each source bearer (LISTENER_CANON.apiCases[].authorization) to a
// binding slot. Only 'browser-token' presents a non-assertion bearer (449); every
// other case presents a freshly minted assertion (459-461, 474, 483, 491).
const CREDENTIAL_SLOTS = [
  { caseId: 'browser-token', kind: 'browser', gap: 'J00_CONSOLE_ORIGIN_CREDENTIAL_BROWSER_TOKEN' },
  { caseId: 'assertion', kind: 'assertion', gap: 'J00_CONSOLE_ORIGIN_CREDENTIAL_ASSERTION' },
  { caseId: 'foreign-origin', kind: 'assertion', gap: 'J00_CONSOLE_ORIGIN_CREDENTIAL_FOREIGN_ORIGIN' },
  { caseId: 'fa-origin', kind: 'assertion', gap: 'J00_CONSOLE_ORIGIN_CREDENTIAL_FA_ORIGIN' },
  { caseId: 'operator', kind: 'assertion', gap: 'J00_CONSOLE_ORIGIN_CREDENTIAL_OPERATOR' },
] as const;
// services/omega-gateway @46e39a4686 (unchanged since 24a61e0424): credential.mjs:14 BEARER `[^\s]{1,8192}`;
// assertion.mjs:16/21/63-65 omega_delegate_ prefix + three base64url segments.
const ASSERTION_PREFIX = 'omega_delegate_';
const BEARER_FORM = /^[\x21-\x7e]{1,8192}$/;
const ASSERTION_FORM = /^omega_delegate_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
/** Valid bound bearer per case id. A value bound to two cases binds neither (single use). */
function readCredentials(binding: unknown): Map<string, string> {
  const found = new Map<string, string>();
  const record = isRecord(binding) && isRecord(binding.credentials) ? binding.credentials : null;
  if (!record) return found;
  const assertions = isRecord(record.assertions) ? record.assertions : {};
  for (const slot of CREDENTIAL_SLOTS) {
    const value = slot.kind === 'browser' ? record.browserToken : assertions[slot.caseId];
    if (typeof value !== 'string' || !BEARER_FORM.test(value)) continue;
    if (slot.kind === 'browser' ? value.startsWith(ASSERTION_PREFIX) : !ASSERTION_FORM.test(value)) continue;
    found.set(slot.caseId, value);
  }
  const values = [...found.values()];
  for (const [caseId, value] of [...found]) if (values.indexOf(value) !== values.lastIndexOf(value)) found.delete(caseId);
  return found;
}
// The app-listener api case (500) presents the SAME literal bearer as 'browser-token' (449):
// it binds to that slot (one Future AGI browser session token, not single-use like an
// assertion). localConsoleOriginReds checks the two source texts are identical.
const APP_CREDENTIAL_CASE = 'browser-token';
const fillReportPath = (text: string, listener: ConsoleOriginListenerCanon) =>
  text.replace('${CSP_REPORT_PATH}', () => listener.cspReportPath);
/**
 * ONE cell per spelling (see ONE CELL PER SPELLING): the 'exact-404' ruled entry whose spelling and
 * methods are exactly a canonical files-only vector's, so the canonical cell carries it; else null.
 */
function mergedRuling(canon: ConsoleOriginCanon, path: string): RuledCell | null {
  return CONSOLE_ORIGIN_RULED.find(ruled => ruled.ruling === 'exact-404' && ruled.target === path
    && canon.filesOnlyStaticPaths.includes(path) && JSON.stringify(ruled.methods) === JSON.stringify(canon.methods)) ?? null;
}

/** Deterministic cell plan: canonical vectors x canonical methods/accepts, plus the ruled cells. */
export function planConsoleOriginMatrix(binding: unknown, canon: ConsoleOriginCanon = CANON,
  listener: ConsoleOriginListenerCanon = LISTENER_CANON, app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON): ConsoleOriginCell[] {
  const cells: ConsoleOriginCell[] = [];
  const record = isRecord(binding) ? binding : {};
  const add = (kind: Kind, vector: number | string, methods: readonly string[], accepts: readonly string[],
    target: string | null, gap: string | null,
    refs: { slot?: ConsoleOriginAssetSlot; ruledId?: string; ruling?: ConsoleOriginRuling; caseId?: string; notApplicable?: boolean } = {}) => {
    for (const method of methods) for (const accept of accepts) cells.push({ id: `${kind}#${vector}|${method}|${accept}`, kind,
      method: method as ConsoleOriginMethod, accept, target, gap, pair: `${kind}#${vector}|${accept}`, slot: refs.slot ?? null,
      ruledId: refs.ruledId ?? null, ruling: refs.ruling ?? null, notApplicable: refs.notApplicable ?? false, caseId: refs.caseId ?? null });
  };
  const documentGap = documentGapOf(record.document);
  canon.absentStaticPaths.forEach((path, index) => add('absent', index, canon.methods, canon.accepts, path, null));
  canon.malformedStaticPaths.forEach((path, index) => add('malformed', index, canon.methods, canon.accepts, path, null));
  // 46e39a4686 (231-260): request vectors only; J00 never plants the files the source plants (52-65).
  canon.filesOnlyStaticPaths.forEach((path, index) => {
    const ruled = mergedRuling(canon, path);
    add('files-only', index, canon.methods, canon.accepts, path, null, ruled ? { ruledId: ruled.id, ruling: ruled.ruling } : {});
  });
  canon.spaRoutes.forEach((path, index) => add('spa', index, canon.methods, canon.accepts, path, documentGap));
  add('spa-dotted', 0, canon.methods, [canon.dottedAccept], canon.dottedSpaRoute, documentGap);
  // Source 97-105 uses fetch(): its default method is GET. Body (107) => bound document.
  canon.documentHeaderRoutes.forEach((path, index) => add('document', index, ['GET'], [canon.documentAccept], path, documentGap));
  const assets = isRecord(record.assets) ? record.assets : {};
  ASSET_SLOTS.forEach((definition, slotIndex) => {
    // Ruling a: asset-encoded#0/#1 (376-379) are planned, never sent, never BLOCKED; the binding is not read.
    if (!definition.applicable) {
      add(definition.kind, slotIndex - 1, canon.methods, [canon.rawDefaultAccept], null, null,
        { slot: definition.slot, notApplicable: true });
      return;
    }
    const { asset, empty } = readAsset(assets[definition.slot], definition.valid);
    // Value-free when unbound, malformed or empty: no name, hash or guessed target is ever produced.
    const targets: (string | null)[] = asset ? definition.targets(asset.name) : [null, null];
    // asset#0 plain / asset#1 unreserved-encoded (355-358).
    targets.forEach((target, form) => add(definition.kind, form, canon.methods, [canon.rawDefaultAccept], target,
      asset ? null : empty ? definition.emptyGap : definition.gap, { slot: definition.slot }));
  });
  canon.missingAssetRoutes.forEach((path, index) => add('asset-missing', index, canon.methods, [canon.rawDefaultAccept], path, null));
  add('api-root', 0, canon.methods, [canon.apiRootAccept], canon.apiRootRoute, null);
  // Source 431 uses fetch() (GET, default accept).
  canon.noLeakRoutes.forEach((path, index) => add('no-leak', index, ['GET'], [canon.rawDefaultAccept], path, null));
  // Listener API cases (445-495): sent only with a bound credential, else BLOCKED value-free.
  const credentials = readCredentials(record);
  for (const apiCase of listener.apiCases) {
    const slot = CREDENTIAL_SLOTS.find(entry => entry.caseId === apiCase.id);
    const bound = !!slot && credentials.has(apiCase.id);
    add('api', apiCase.id, [apiCase.method], [canon.rawDefaultAccept], bound ? apiCase.path : null,
      bound ? null : slot?.gap ?? 'J00_CONSOLE_ORIGIN_CREDENTIAL', { caseId: apiCase.id });
  }
  // CSP report sink (525-549): no credential involved, always sent.
  for (const report of listener.cspReports) add('csp-report', report.id, [report.method], [canon.rawDefaultAccept],
    fillReportPath(report.path, listener), null, { caseId: report.id });
  // App listener (497-523), sent to binding.appListenerUrl only. The api case also needs the
  // bound browser token; the static 404 routes need no credential (517 sends none).
  const appGap = consoleOriginAppListener(record).gap;
  const apiGap = appGap ?? (credentials.has(APP_CREDENTIAL_CASE) ? null
    : CREDENTIAL_SLOTS.find(slot => slot.caseId === APP_CREDENTIAL_CASE)!.gap);
  add('app-api', app.api.id, [app.api.method], [canon.rawDefaultAccept], apiGap ? null : app.api.path, apiGap, { caseId: app.api.id });
  app.staticRoutes.forEach((path, index) => add('app-absent', index, app.staticMethods, [app.staticAccept],
    appGap ? null : path, appGap));
  // Rulings (CONSOLE_ORIGIN_RULED); a '404-or-spa' 200 is compared with the bound document.
  // A ruled spelling that is now a canonical vector is already planned ONCE, as that canonical cell (mergedRuling).
  for (const ruled of CONSOLE_ORIGIN_RULED) if (mergedRuling(canon, ruled.target) !== ruled) add('ruled', ruled.id, ruled.methods,
    ruled.methods.includes('OPTIONS') ? [canon.rawDefaultAccept] : canon.accepts, ruled.target,
    ruled.ruling === '404-or-spa' ? documentGap : null, { ruledId: ruled.id, ruling: ruled.ruling });
  return cells;
}

const HEADER_END = Buffer.from('\r\n\r\n', 'latin1');
export type RawHeader = readonly [name: string, value: string];
/**
 * Literal HTTP/1.1 request: the request-target is the vector verbatim, never parsed or
 * normalized. Every request asks for identity content: body predicates must read
 * plain content, and any coded answer is a contract failure (see responseContent).
 */
export function buildRawRequest(method: ConsoleOriginMethod, target: string, host: string, accept: string,
  headers: readonly RawHeader[] = [], body: Buffer = Buffer.alloc(0)): Buffer {
  if (!/^[\x21-\x7e]+$/.test(target) || !/^[\x21-\x7e]+$/.test(host) || !/^[\x20-\x7e]+$/.test(accept)
    || headers.some(([name, value]) => !/^[A-Za-z][A-Za-z-]*$/.test(name) || !/^[\x20-\x7e]+$/.test(value)
      || /^(?:host|accept|accept-encoding|content-length|connection|transfer-encoding)$/i.test(name)))
    throw new Error('unsendable Console-origin request (values withheld)');
  const extra = headers.map(([name, value]) => `${name}: ${value}\r\n`).join('');
  const length = method === 'POST' || body.length > 0 ? `Content-Length: ${body.length}\r\n` : '';
  return Buffer.concat([Buffer.from(`${method} ${target} HTTP/1.1\r\nHost: ${host}\r\nAccept: ${accept}\r\n`
    + `Accept-Encoding: identity\r\n${extra}${length}Connection: close\r\n\r\n`, 'latin1'), body]);
}
/**
 * The exact bytes a cell must put on the wire. Credentials are read from the binding
 * here, at send/evaluation time, and never stored in a cell. `target` overrides only
 * the request-target (local controls).
 */
export function buildCellRequest(cell: ConsoleOriginCell, input: ConsoleOriginRunInput,
  listener: ConsoleOriginListenerCanon = LISTENER_CANON, target: string | null = cell.target,
  app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON): Buffer {
  const origin = consoleOriginBindingUrl(input.binding), destination = consoleOriginCellOrigin(cell, input.binding);
  if (!origin || !destination || target === null) throw new Error('unsendable Console-origin request (values withheld)');
  const host = new URL(destination).host;
  const fill = (text: string) => fillReportPath(text, listener).replace('${CONSOLE}', () => origin)
    .replace('${FA}', () => input.publicOrigin);
  if (cell.kind === 'app-api') {
    // 499-501: the browser token (same slot as 449) and Origin FA (= the J00 public origin).
    const credential = readCredentials(input.binding).get(APP_CREDENTIAL_CASE);
    if (cell.caseId !== app.api.id || credential === undefined) throw new Error('unsendable Console-origin request (values withheld)');
    return buildRawRequest(cell.method, target, host, cell.accept,
      [['Authorization', `Bearer ${credential}`], ['Origin', fill(app.api.origin)]]);
  }
  if (cell.kind === 'api') {
    const apiCase = listener.apiCases.find(entry => entry.id === cell.caseId);
    const credential = cell.caseId === null ? undefined : readCredentials(input.binding).get(cell.caseId);
    if (!apiCase || credential === undefined) throw new Error('unsendable Console-origin request (values withheld)');
    const headers: RawHeader[] = [['Authorization', `Bearer ${credential}`]];
    if (apiCase.origin !== null) headers.push(['Origin', fill(apiCase.origin)]);
    return buildRawRequest(cell.method, target, host, cell.accept, headers);
  }
  if (cell.kind === 'csp-report') {
    const report = listener.cspReports.find(entry => entry.id === cell.caseId);
    if (!report) throw new Error('unsendable Console-origin request (values withheld)');
    const body = typeof report.body === 'string' ? fill(report.body) : report.body.repeat.repeat(report.body.count);
    return buildRawRequest(cell.method, target, host, cell.accept,
      [['Content-Type', report.contentType ?? FETCH_STRING_BODY_TYPE]], Buffer.from(body, 'utf8'));
  }
  // Ruling c: an arbitrary Origin plus a preflight method, so a credentialed reflection is observable.
  if (cell.kind === 'ruled' && cell.ruling === 'no-credentialed-cors') return buildRawRequest(cell.method, target, host, cell.accept,
    [['Origin', PREFLIGHT_ORIGIN], ['Access-Control-Request-Method', PREFLIGHT_METHOD]]);
  return buildRawRequest(cell.method, target, host, cell.accept);
}
/** Byte-for-byte request-line self-check against the vector. */
export function sentTargetIs(written: Buffer, method: string, target: string): boolean {
  const end = written.indexOf('\r\n');
  return end > 0 && written.subarray(0, end).equals(Buffer.from(`${method} ${target} HTTP/1.1`, 'latin1'));
}
/** Whole-request self-check: Host, Accept-Encoding, Authorization, Origin, Content-Type and body included. */
function sentRequestIs(cell: ConsoleOriginCell, written: Buffer, input: ConsoleOriginRunInput,
  listener: ConsoleOriginListenerCanon, app: ConsoleOriginAppListenerCanon): boolean {
  try { return written.equals(buildCellRequest(cell, input, listener, cell.target, app)); } catch { return false; }
}
function decodeChunked(data: Buffer): { body: Buffer; complete: boolean } | null {
  const parts: Buffer[] = [];
  for (let at = 0; ;) {
    const lineEnd = data.indexOf('\r\n', at);
    if (lineEnd < 0) return { body: Buffer.concat(parts), complete: false };
    const size = owsTrim(data.subarray(at, lineEnd).toString('latin1').split(';')[0]);
    if (!/^[0-9a-fA-F]{1,8}$/.test(size)) return null;
    const length = parseInt(size, 16), start = lineEnd + 2, stop = start + length;
    if (length === 0) return { body: Buffer.concat(parts), complete: data.indexOf(HEADER_END, lineEnd) >= 0 };
    if (data.length < stop + 2) return { body: Buffer.concat([...parts, data.subarray(start, stop)]), complete: false };
    if (data[stop] !== 0x0d || data[stop + 1] !== 0x0a) return null;
    parts.push(data.subarray(start, stop));
    at = stop + 2;
  }
}
export interface ParsedRawResponse {
  status: number; headers: Record<string, string>; body: Buffer; bytesAfterHeaders: number; complete: boolean;
  /** 1xx interim header blocks skipped before the final response. */
  interim: number;
}
/** Strip HTTP optional whitespace (SP and HTAB only, RFC 9110 5.6.3), never Unicode whitespace such as 0xA0. */
export function owsTrim(value: string): string {
  return value.replace(/^[ \t]+|[ \t]+$/g, '');
}
/**
 * Pure HTTP/1.x response parser over the raw bytes received (no client-side body skipping).
 * Interim 1xx blocks (100, 102, 103, ...) carry no body and are skipped: the FINAL response
 * is parsed, and HEAD body bytes are counted after its header block. An interim block with
 * no final block yet is null (keep reading). 101 is returned as-is: the caller records it
 * as a transport failure (observeExchange / checkCell), never as an answer.
 */
export function parseRawResponse(received: Buffer, head: boolean, closed: boolean): ParsedRawResponse | null {
  for (let start = 0, interim = 0; ; interim++) {
    const end = received.indexOf(HEADER_END, start);
    if (end < 0) return null;
    const [statusLine, ...lines] = received.subarray(start, end).toString('latin1').split('\r\n');
    const match = /^HTTP\/1\.[01] (\d{3})(?: [^\r\n]*)?$/.exec(statusLine);
    if (!match) return null;
    const headers = Object.create(null) as Record<string, string>;
    for (const line of lines) {
      const colon = line.indexOf(':');
      if (colon <= 0) return null;
      // RFC 9112 5.1: no whitespace inside or before the field name; RFC 9110 5.6.3: OWS is SP/HTAB only.
      const rawName = line.slice(0, colon);
      if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(rawName)) return null;
      const name = rawName.toLowerCase(), value = owsTrim(line.slice(colon + 1));
      headers[name] = name in headers ? `${headers[name]}, ${value}` : value;
    }
    const status = Number(match[1]);
    start = end + HEADER_END.length;
    if (status >= 100 && status < 200 && status !== 101) continue;
    const rest = received.subarray(start);
    const base = { status, headers, bytesAfterHeaders: rest.length, interim };
    if (head || status === 204 || status === 304 || status < 200) return { ...base, body: Buffer.alloc(0), complete: true };
    if (/(?:^|,)\s*chunked\s*$/i.test(headers['transfer-encoding'] ?? '')) {
      const decoded = decodeChunked(rest);
      return decoded && { ...base, ...decoded };
    }
    if ('content-length' in headers) {
      if (!/^\d+$/.test(headers['content-length'])) return null;
      const length = Number(headers['content-length']);
      return { ...base, body: rest.subarray(0, length), complete: rest.length >= length };
    }
    return { ...base, body: rest, complete: closed };
  }
}
export interface RawExchange { written: Buffer; received: Buffer; closed: boolean; transportError: boolean }
export type RawSend = (origin: string, request: Buffer, head: boolean) => Promise<RawExchange>;
/** The socket surface the transport uses (node:net Socket in production, a scripted fake offline). */
export interface RawSocket {
  on(event: 'data', listener: (chunk: Buffer) => void): unknown;
  once(event: 'connect' | 'end' | 'close' | 'error', listener: () => void): unknown;
  write(bytes: Buffer): unknown;
  destroy(): unknown;
}
export interface RawTimers { set(callback: () => void, ms: number): unknown; clear(handle: unknown): void }
export const RAW_TIMEOUT_MS = 10_000;
/**
 * Transport state machine, socket- and clock-injectable so the offline controls drive the
 * SAME code as the live path. One write of the literal request bytes; every received byte
 * kept. GET (and OPTIONS/POST) ends on its framing, after any 1xx blocks. HEAD ends ONLY on
 * server EOF ('end'): with Connection: close a quiet open socket proves nothing about a
 * later body, so no grace period exists and the 10 s timeout is a transport failure.
 * 'close' without 'end', or 'error', is a transport failure too.
 */
export function driveRawExchange(socket: RawSocket, request: Buffer, head: boolean, timers: RawTimers,
  done: (exchange: RawExchange) => void): void {
  const written: Buffer[] = [], received: Buffer[] = [];
  const state = { closed: false, transportError: false, finished: false };
  const finish = () => {
    if (state.finished) return;
    state.finished = true; timers.clear(timer); socket.destroy();
    done({ written: Buffer.concat(written), received: Buffer.concat(received), closed: state.closed, transportError: state.transportError });
  };
  const timer = timers.set(() => { state.transportError = true; finish(); }, RAW_TIMEOUT_MS);
  socket.once('connect', () => { written.push(Buffer.from(request)); socket.write(request); });
  socket.on('data', chunk => {
    received.push(chunk);
    if (!head && parseRawResponse(Buffer.concat(received), false, false)?.complete) finish();
  });
  socket.once('end', () => { state.closed = true; finish(); });
  socket.once('close', () => { if (!state.closed) state.transportError = true; finish(); });
  socket.once('error', () => { state.transportError = true; finish(); });
}
const NODE_TIMERS: RawTimers = {
  set: (callback, ms) => setTimeout(callback, ms),
  clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
};
/**
 * Raw-path transport: node:net socket. URL-based clients (fetch, Playwright request,
 * `new URL`) normalize '..', '.' and some escapes; node:http additionally skips any HEAD
 * body. Here nothing is parsed on the way out (see driveRawExchange for completion).
 */
export const rawExchange: RawSend = (origin, request, head) => {
  const url = new URL(origin);
  // The spec refuses non-http bindings BLOCKED before any cell; never fall back to TLS clients.
  if (url.protocol !== 'http:')
    return Promise.resolve({ written: Buffer.alloc(0), received: Buffer.alloc(0), closed: false, transportError: true });
  const host = url.hostname.replace(/^\[(.*)\]$/, '$1'), port = Number(url.port || 80);
  return new Promise(resolve => driveRawExchange(connect({ host, port }), request, head, NODE_TIMERS, resolve));
};
export type RawScriptEvent = { at: number; event: 'data' | 'end' | 'close' | 'error'; bytes?: Buffer };
/**
 * Offline transport harness: driveRawExchange against a scripted socket on a virtual
 * clock (ms). 'connect' fires at 0; timers and events interleave by time; like a real
 * socket, nothing is delivered after destroy(). No socket, no network, no real time.
 */
export function simulateRawExchange(request: Buffer, head: boolean, script: readonly RawScriptEvent[]):
  { exchange: RawExchange | null; finishedAt: number | null } {
  const clock = { now: 0, destroyed: false, finishedAt: null as number | null, exchange: null as RawExchange | null };
  const listeners = new Map<string, { listener: (chunk: Buffer) => void; once: boolean }[]>();
  const listen = (once: boolean) => (event: string, listener: (chunk: Buffer) => void) => {
    listeners.set(event, [...listeners.get(event) ?? [], { listener, once }]);
  };
  const emit = (event: string, bytes: Buffer) => {
    for (const entry of listeners.get(event) ?? []) {
      if (entry.once) listeners.set(event, (listeners.get(event) ?? []).filter(other => other !== entry));
      entry.listener(bytes);
    }
  };
  const timers: { at: number; callback: () => void; live: boolean }[] = [];
  const socket: RawSocket = { on: listen(false), once: listen(true), write: () => true, destroy: () => { clock.destroyed = true; } };
  driveRawExchange(socket, request, head, {
    set: (callback, ms) => { const timer = { at: clock.now + ms, callback, live: true }; timers.push(timer); return timer; },
    clear: handle => { (handle as { live: boolean }).live = false; },
  }, exchange => { clock.exchange = exchange; clock.finishedAt = clock.now; });
  const events: { at: number; event: string; bytes?: Buffer }[] = [{ at: 0, event: 'connect' }, ...script].sort((a, b) => a.at - b.at);
  const due = (limit: number) => timers.filter(timer => timer.live && timer.at <= limit).sort((a, b) => a.at - b.at)[0];
  for (let index = 0; clock.exchange === null; index++) {
    const limit = index < events.length ? events[index].at : Infinity;
    for (let timer = due(limit); timer && clock.exchange === null; timer = due(limit)) {
      clock.now = timer.at; timer.live = false; timer.callback();
    }
    if (clock.exchange !== null || clock.destroyed || index >= events.length) break;
    clock.now = events[index].at;
    emit(events[index].event, events[index].bytes ?? Buffer.alloc(0));
  }
  return { exchange: clock.exchange, finishedAt: clock.finishedAt };
}
export function observeExchange(cell: ConsoleOriginCell, exchange: RawExchange): ConsoleOriginObservation {
  const head = cell.method === 'HEAD';
  const parsed = parseRawResponse(exchange.received, head, exchange.closed);
  // A HEAD answer is evidence only after server EOF (no later body can follow), and a
  // final status below 200 (101 protocol switch) is no HTTP answer to this request.
  const transportError = exchange.transportError || !parsed?.complete || (head && !exchange.closed) || (parsed?.status ?? 0) < 200;
  return { cell: cell.id, written: exchange.written, status: parsed?.status ?? 0, headers: parsed?.headers ?? {},
    body: parsed?.body ?? Buffer.alloc(0), bytesAfterHeaders: parsed?.bytesAfterHeaders ?? -1, transportError };
}
/** Network phase. Call only after localConsoleOriginReds() returned no failures. */
export async function runConsoleOriginMatrix(input: ConsoleOriginRunInput, cells: readonly ConsoleOriginCell[],
  send: RawSend = rawExchange, listener: ConsoleOriginListenerCanon = LISTENER_CANON,
  app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON): Promise<ConsoleOriginObservation[]> {
  if (!consoleOriginBindingUrl(input.binding)) throw new Error('unbound Console-origin listener (values withheld)');
  const queue = cells.filter(cell => cell.target !== null);
  const observations: ConsoleOriginObservation[] = [];
  let next = 0;
  const worker = async () => {
    while (next < queue.length) {
      const cell = queue[next++];
      // The listener this cell belongs to: the Console listener, or the app listener (498 `base`).
      const destination = consoleOriginCellOrigin(cell, input.binding);
      let request: Buffer | null = null;
      try { request = destination ? buildCellRequest(cell, input, listener, cell.target, app) : null; } catch { request = null; }
      if (!destination || !request) {
        observations.push({ cell: cell.id, written: Buffer.alloc(0), status: 0, headers: {}, body: Buffer.alloc(0),
          bytesAfterHeaders: -1, transportError: true });
        continue;
      }
      observations.push(observeExchange(cell, await send(destination, request, cell.method === 'HEAD')));
    }
  };
  await Promise.all(Array.from({ length: 4 }, worker));
  return observations;
}

function jsonError(body: Buffer | null): unknown {
  if (!body) return undefined;
  try { return (JSON.parse(body.toString('utf8')) as { error?: unknown }).error; } catch { return undefined; }
}
const fileOf = (bytes: Buffer): ConsoleOriginFile => ({ byteLength: bytes.length, sha256: sha256(bytes) });
/** Positive body predicate: NON-EMPTY content equal to the bound file (an empty answer never matches). */
const sameFile = (bytes: Buffer | null, file: ConsoleOriginFile | null) =>
  !!bytes && !!file && bytes.length > 0 && bytes.length === file.byteLength && sha256(bytes) === file.sha256;

/** Upper bound on decoded content per response (a coded answer is never trusted to be small). */
export const CONTENT_DECODE_LIMIT = 16 * 1024 * 1024;
function decodeCoding(coding: string, bytes: Buffer): Buffer | null {
  const options = { maxOutputLength: CONTENT_DECODE_LIMIT };
  try {
    if (coding === 'gzip' || coding === 'x-gzip') return gunzipSync(bytes, options);
    if (coding === 'br') return brotliDecompressSync(bytes, options);
    // RFC 9110 8.4.1.2 'deflate' is the zlib format; raw deflate is accepted as clients do.
    if (coding === 'deflate') {
      try { return inflateSync(bytes, options); } catch { return inflateRawSync(bytes, options); }
    }
  } catch { return null; }
  return null; // Unknown coding (compress, zstd, '', ...): never read as plain bytes.
}
/**
 * The response CONTENT every body predicate reads. `coded` is true for ANY
 * non-identity Content-Encoding or any Transfer-Encoding other than plain chunked (J00 sent
 * 'Accept-Encoding: identity'): the caller records L3.09-content-encoding. `body` is the
 * decoded content (codings undone in reverse order, each bounded by CONTENT_DECODE_LIMIT), or
 * null when it cannot be read (unknown/malformed coding, over the limit, a transfer coding),
 * so every body predicate fails closed instead of scanning coded bytes as if plain.
 */
export function responseContent(observation: Pick<ConsoleOriginObservation, 'headers' | 'body'>): { body: Buffer | null; coded: boolean } {
  const tokens = (value: string | undefined) => value === undefined ? [] : value.split(',').map(token => owsTrim(token).toLowerCase());
  const transfer = tokens(observation.headers['transfer-encoding']);
  const codings = tokens(observation.headers['content-encoding']).filter(token => token !== 'identity');
  if (transfer.length > 0 && !(transfer.length === 1 && transfer[0] === 'chunked')) return { body: null, coded: true };
  if (codings.length === 0) return { body: observation.body, coded: false };
  let body: Buffer | null = observation.body;
  for (const coding of [...codings].reverse()) if (body) body = decodeCoding(coding, body);
  return { body, coded: true };
}

type CspDirective = { name: string; value: string[] };
/**
 * W3C CSP3 "parse a serialized CSP": strictly split on ';', strip ASCII whitespace, skip empty
 * and non-ASCII tokens, ASCII-lowercase the name, split the value on ASCII whitespace.
 * Duplicates are KEPT here (CSP3 keeps the first) so the caller can refuse them.
 */
export function parseCspPolicy(serialized: string): CspDirective[] {
  const directives: CspDirective[] = [];
  for (const raw of serialized.split(';')) {
    const token = raw.replace(/^[\t\n\f\r ]+|[\t\n\f\r ]+$/g, '');
    if (!token || /[^\x00-\x7f]/.test(token)) continue;
    const [name, ...value] = token.split(/[\t\n\f\r ]+/);
    directives.push({ name: name.toLowerCase(), value });
  }
  return directives;
}
type CellContext = { canon: ConsoleOriginCanon; listener: ConsoleOriginListenerCanon; app: ConsoleOriginAppListenerCanon;
  publicOrigin: string; document: ConsoleOriginFile | null; assets: Partial<Record<ConsoleOriginAssetSlot, ConsoleOriginAsset>> };
function documentCspHolds(observation: ConsoleOriginObservation, context: CellContext): boolean {
  const csp = observation.headers['content-security-policy'], expected = context.canon.outcomes.documentHeaders;
  if (typeof csp !== 'string') return false;
  // Expected values come from the canonical constants ONLY (FA = bound public origin,
  // CSP_REPORT_PATH = console-origin.mjs:37), never from the response under test.
  const fill = (template: string) => template.replace('${FA}', () => context.publicOrigin)
    .replace('${CSP_REPORT_PATH}', () => context.listener.cspReportPath);
  const canonical = context.canon.documentCspDirectives.map(fill);
  // (a) The canonical literal check (119), unchanged.
  const literal = csp.split('; ');
  if (!canonical.every(directive => literal.includes(directive))) return false;
  // (b) CSP3 parse. ',' starts a second enforced policy (CSP3 "parse a response's CSP list").
  if (csp.includes(',')) return false;
  const parsed = parseCspPolicy(csp);
  const named = (name: string) => parsed.filter(directive => directive.name === name);
  // Every canonical directive EXACTLY once with exactly its canonical value: one report-uri,
  // equal to CSP_REPORT_PATH, and no earlier duplicate that a CSP3 user agent would apply.
  const once = canonical.every(directive => {
    const [want] = parseCspPolicy(directive), found = want ? named(want.name) : [];
    return !!want && found.length === 1 && JSON.stringify(found[0].value) === JSON.stringify(want.value);
  });
  return once && named('report-uri').length === 1 && named('report-to').length === 0
    && !new RegExp(expected.forbiddenCsp).test(csp)
    && observation.headers['content-security-policy-report-only'] === fill(expected.reportOnly);
}
/** A strict JSON value read from the raw bytes (parseStrictJson): duplicates are refused, so a member list is exact. */
export type StrictJson = { kind: 'boolean'; value: boolean } | { kind: 'string'; text: string; start: number; end: number }
  | { kind: 'object'; members: { key: string; value: StrictJson }[] };
/**
 * A strict JSON subset read from the RAW bytes (latin1: one char per byte), never through JSON.parse.
 * JSON.parse keeps only the LAST copy of a repeated member, so an earlier copy could carry any bytes past
 * a check of the parsed value. Accepted: objects, strings and booleans only
 * (a number, null or an array refuses); a string is printable ASCII with no '\' (no escape can spell
 * what the raw scan refuses; `start`/`end` locate its raw text); a member name at most ONCE per object,
 * at ANY depth; objects nested at most `maxDepth` deep (bounded recursion); JSON whitespace; nothing
 * after the value. Anything else is null.
 */
export function parseStrictJson(raw: Buffer, maxDepth: number): StrictJson | null {
  const text = raw.toString('latin1');
  let at = 0;
  const space = () => { while (at < text.length && ' \t\n\r'.includes(text[at])) at++; };
  const string = (): Extract<StrictJson, { kind: 'string' }> | null => {
    if (text[at] !== '"') return null;
    const start = ++at;
    for (; at < text.length && text[at] !== '"'; at++) {
      const code = text.charCodeAt(at);
      if (code < 0x20 || code > 0x7e || code === 0x5c) return null;
    }
    if (at >= text.length) return null;
    const end = at++;
    return { kind: 'string', text: text.slice(start, end), start, end };
  };
  const value = (depth: number): StrictJson | null => {
    if (text[at] === '"') return string();
    if (text.startsWith('true', at)) { at += 4; return { kind: 'boolean', value: true }; }
    if (text.startsWith('false', at)) { at += 5; return { kind: 'boolean', value: false }; }
    if (text[at] !== '{' || depth >= maxDepth) return null;
    at++;
    const members: { key: string; value: StrictJson }[] = [], names = new Set<string>();
    space();
    if (text[at] === '}') { at++; return { kind: 'object', members }; }
    for (;;) {
      space();
      const key = string();
      if (!key || names.has(key.text)) return null;
      names.add(key.text);
      space();
      if (text[at++] !== ':') return null;
      space();
      const child = value(depth + 1);
      if (!child) return null;
      members.push({ key: key.text, value: child });
      space();
      const next = text[at++];
      if (next === '}') return { kind: 'object', members };
      if (next !== ',') return null;
    }
  };
  space();
  const root = value(0);
  space();
  return root && at === text.length ? root : null;
}
/**
 * Ruling b body predicate: the healthz/readyz GET body carries ONLY a status. An ALLOWLIST over the RAW
 * bytes, fail-closed (unreadable or over STATUS_BODY_LIMIT fails). Allowed shapes:
 * - an empty body (it carries nothing);
 * - ONE status word;
 * - a JSON object (parseStrictJson: no member twice at any depth, no escape, no number/null/array)
 *   whose members are only: status (a status word); ok, live, ready (booleans); checks (an object of
 *   short component names, never an identity name (IDENTITY_KEY), to a status word or a boolean);
 *   requestId (ONLY when the HTTP status is >= 400: exactly 32 lowercase hex). Any other key fails
 *   (fqdn, token, node_name, served_by, ...).
 * The gateway's probe bodies hold: {"live":true} (server.mjs:255 @46e39a4686, unchanged since 24a61e0424), {"ready":true,"checks":
 * {"ee":"ok","exchange":"ok"}} (lifecycle.mjs:60) and, not ready or draining at 503, {"ready":false,
 * "checks":{"ee":"fail",...}} or {"ready":false,"checks":{"draining":true}} (lifecycle.mjs:52: a BOOLEAN
 * check, hence 'or a boolean') with requestId appended (server.mjs:201).
 * INTERPRETATION of ruling b (labelled): requestId is the gateway's per-request trace id (trace.mjs:38,
 * 16 random bytes as hex, also the x-request-id header), not a version, build SHA, hostname or address,
 * so it is allowed where respond() puts it (>= 400) and nowhere else; its value is the only raw text the
 * scan below skips.
 * Forbidden-pattern scan over the raw bytes, whatever the structure said: no digit or '.' anywhere, no
 * ':', '/', '\' or '-' inside any quoted token, no hex run of 7+ (a build SHA spelled a-f), no 'localhost'.
 * A status word is letters/underscore only, 1-32. Residual (disclosed): a hostname spelled in letters
 * alone is indistinguishable from a status word or a component name, as a value or check name.
 */
export const STATUS_BODY_LIMIT = 512;
const STATUS_WORD = /^[A-Za-z_]{1,32}$/;
const HEX_RUN = /[0-9a-f]{7,}/i;
const IDENTITY_KEY = /version|build|commit|sha|revision|host|addr|instance|region|zone|image|digest|release|uptime|fqdn|node|pod|served|machine|token|secret|cluster|^(?:ip|pid|git|rev|tag|ver|server|name)$/i;
/** The gateway's trace id (trace.mjs:38): respond() appends it as requestId to every body with status >= 400 (server.mjs:201). */
const REQUEST_ID = /^[0-9a-f]{32}$/;
const statusWord = (value: string) => STATUS_WORD.test(value) && !HEX_RUN.test(value) && value.toLowerCase() !== 'localhost';
const isStatusWord = (value: StrictJson) => value.kind === 'string' && statusWord(value.text);
const STATUS_MEMBERS = new Map<string, (value: StrictJson, status: number) => boolean>([
  ['status', isStatusWord],
  ['ok', value => value.kind === 'boolean'], ['live', value => value.kind === 'boolean'], ['ready', value => value.kind === 'boolean'],
  ['checks', value => value.kind === 'object' && value.members.every(check => statusWord(check.key) && !IDENTITY_KEY.test(check.key)
    && (check.value.kind === 'boolean' || isStatusWord(check.value)))],
  ['requestId', (value, status) => status >= 400 && value.kind === 'string' && REQUEST_ID.test(value.text)],
]);
export function statusOnlyBody(content: Buffer | null, status: number): boolean {
  if (content === null || content.length > STATUS_BODY_LIMIT) return false;
  const raw = content.toString('latin1'), text = raw.replace(/^[\t\n\r ]+|[\t\n\r ]+$/g, '');
  if (text === '') return true;
  let scanned = text;
  if (text.startsWith('{')) {
    const root = parseStrictJson(content, 2);
    if (root?.kind !== 'object' || !root.members.every(({ key, value }) => STATUS_MEMBERS.get(key)?.(value, status) === true)) return false;
    const id = root.members.find(member => member.key === 'requestId')?.value;
    scanned = id?.kind === 'string' ? raw.slice(0, id.start) + raw.slice(id.end) : raw;
  } else if (!statusWord(text)) return false;
  return !/[0-9.]/.test(scanned) && !HEX_RUN.test(scanned) && !/localhost/i.test(scanned)
    && [...scanned.matchAll(/"([^"]*)"/g)].every(([, token]) => !/[:/\\-]/.test(token));
}
/**
 * Ruling c: no credentialed wildcard, no credentialed 'null' origin (a sandboxed iframe or file: page sends
 * Origin: null), and no credentialed reflection of the arbitrary Origin J00 sent (per comma token).
 */
function credentialedCorsRefused(headers: Readonly<Record<string, string>>): boolean {
  const tokens = (value: string | undefined) => (value ?? '').split(',').map(token => owsTrim(token).toLowerCase());
  return !tokens(headers['access-control-allow-credentials']).includes('true')
    || !tokens(headers['access-control-allow-origin']).some(origin => origin === '*' || origin === 'null' || origin === PREFLIGHT_ORIGIN);
}
type Outcomes = ConsoleOriginCanon['outcomes'];
/**
 * The not-found shape beyond the status (outcomes.notFound): headers (272-278), HEAD empty (281) and the JSON
 * error member. This was the whole canonical contract at 24a61e0424 (its 241); rulings d and e build on exactly
 * this piece, unchanged by the rebind.
 */
function notFoundShapeHolds(cell: ConsoleOriginCell, headers: Readonly<Record<string, string>>, content: Buffer | null,
  expected: Outcomes['notFound']): boolean {
  return headers['content-type'] === expected.contentType && headers['cache-control'] === expected.cacheControl
    && headers['x-content-type-options'] === expected.contentTypeOptions
    && (cell.method === 'HEAD' ? content?.toString('utf8') === expected.headBody : jsonError(content) === expected.error);
}
/**
 * The canonical not-found contract @46e39a4686 for the absent, malformed AND files-only vectors (267-293):
 * notFoundShapeHolds AND a GET body of EXACTLY the members `keys` (286-290: Object.keys(body).sort() deepEqual
 * ['error', 'requestId']), error equal to `error` (291) and requestId matching `requestId` (292: the per-request
 * trace id, 283-284). J00 strictness (labelled, beyond the source's JSON.parse): the body is read with
 * parseStrictJson over the raw bytes, so a member twice (JSON.parse keeps only the last copy) or an escape refuses.
 */
function notFoundContractHolds(cell: ConsoleOriginCell, headers: Readonly<Record<string, string>>, content: Buffer | null,
  expected: Outcomes['notFound']): boolean {
  if (!notFoundShapeHolds(cell, headers, content, expected)) return false;
  if (cell.method === 'HEAD') return true;
  // A canon without the key set or pattern (a mutated copy) refuses: fail closed, never a TypeError.
  if (!Array.isArray(expected.keys) || typeof expected.requestId !== 'string') return false;
  const root = content ? parseStrictJson(content, 1) : null;
  if (root?.kind !== 'object') return false;
  const members = root.members;
  const member = (key: string) => members.find(entry => entry.key === key)?.value;
  const error = member('error'), requestId = member('requestId');
  return JSON.stringify(members.map(entry => entry.key).sort()) === JSON.stringify([...expected.keys].sort())
    && error?.kind === 'string' && error.text === expected.error
    && requestId?.kind === 'string' && new RegExp(expected.requestId).test(requestId.text);
}
/**
 * Rulings d and e (404 branch): the not-found shape AND exactly the gateway's not-found body,
 * {"error":"not_found"} or {"error":"not_found","requestId":"<32 lowercase hex>"} (server.mjs:201, 266):
 * no other member, none twice, no escape (parseStrictJson). A listing or file bytes in any member fails.
 */
function ruledNotFoundHolds(cell: ConsoleOriginCell, headers: Readonly<Record<string, string>>, content: Buffer | null,
  expected: Outcomes['notFound']): boolean {
  if (!notFoundShapeHolds(cell, headers, content, expected)) return false;
  if (cell.method === 'HEAD') return true;
  const root = content ? parseStrictJson(content, 1) : null;
  return root?.kind === 'object' && root.members.some(member => member.key === 'error') && root.members.every(({ key, value }) =>
    value.kind === 'string' && (key === 'error' ? value.text === expected.error : key === 'requestId' && REQUEST_ID.test(value.text)));
}
/** The canonical SPA answer's header names (outcomes.spa; Content-Length = the bound document's) this answer does not carry. */
function spaHeadersDiffering(headers: Readonly<Record<string, string>>, expected: Outcomes['spa'], document: ConsoleOriginFile | null): string[] {
  const holds: [name: string, held: boolean][] = [['content-type', headers['content-type'] === expected.contentType],
    ['cache-control', headers['cache-control'] === expected.cacheControl],
    ['content-security-policy', new RegExp(expected.csp).test(headers['content-security-policy'] ?? '')],
    ['content-length', headers['content-length'] === String(document?.byteLength ?? -1)]];
  return holds.filter(([, held]) => !held).map(([name]) => name);
}
const spaHeadersHold = (headers: Readonly<Record<string, string>>, expected: Outcomes['spa'], document: ConsoleOriginFile | null) =>
  spaHeadersDiffering(headers, expected, document).length === 0;
function checkCell(cell: ConsoleOriginCell, observation: ConsoleOriginObservation, context: CellContext): string[] {
  const failures: string[] = [], headers = observation.headers, outcomes = context.canon.outcomes;
  const fail = (condition: boolean, check: string) => { if (condition) failures.push(check); };
  fail(observation.transportError || observation.status < 200, 'L3.09-transport');
  fail(cell.method === 'HEAD' && (observation.bytesAfterHeaders !== 0 || observation.body.length !== 0), 'L3.09-head-body');
  // Every body predicate below reads `content` (decoded, or null => fails closed).
  const { body: content, coded } = responseContent(observation);
  fail(coded, 'L3.09-content-encoding');
  const asset = cell.slot ? context.assets[cell.slot] ?? null : null;
  switch (cell.kind) {
  case 'absent': case 'malformed': case 'files-only': {
    const expected = outcomes.notFound;
    fail(observation.status !== expected.status, 'L3.09-not-found-status');
    fail(!notFoundContractHolds(cell, headers, content, expected), 'L3.09-not-found-contract');
    // ONE cell for a ruled spelling that is now a canonical vector (mergedRuling): ruling d runs too (stricter of both).
    if (cell.ruling === 'exact-404') {
      fail(observation.status !== expected.status, 'L3.09-ruled-404-status');
      fail(!ruledNotFoundHolds(cell, headers, content, expected), 'L3.09-ruled-404-contract');
    } else if (cell.ruling !== null) failures.push('L3.09-ruled-unknown');
    break;
  }
  case 'spa': {
    const expected = outcomes.spa;
    fail(observation.status !== expected.status, 'L3.09-spa-status');
    fail(!spaHeadersHold(headers, expected, context.document), 'L3.09-spa-contract');
    fail(cell.method === 'GET' && !sameFile(content, context.document), 'L3.09-spa-document');
    break;
  }
  case 'spa-dotted':
    fail(observation.status !== outcomes.dotted.status, 'L3.09-spa-status');
    fail(cell.method === 'GET' && !sameFile(content, context.document), 'L3.09-spa-document');
    break;
  case 'document': {
    const expected = outcomes.documentHeaders;
    fail(observation.status !== expected.status, 'L3.09-document-status');
    fail(!documentCspHolds(observation, context), 'L3.09-document-csp');
    fail(headers['x-content-type-options'] !== expected.contentTypeOptions || headers['referrer-policy'] !== expected.referrerPolicy
      || headers['cache-control'] !== expected.cacheControl
      || (headers['access-control-allow-origin'] ?? null) !== expected.allowOrigin, 'L3.09-document-headers');
    // Source 107 (`<title>Console</title>` of the fixture document) => the bound deployed document.
    fail(!sameFile(content, context.document), 'L3.09-document-body');
    break;
  }
  case 'asset': {
    const expected = outcomes.asset;
    fail(observation.status !== expected.status, 'L3.09-asset-status');
    fail(headers['content-type'] !== expected.contentType || headers['cache-control'] !== expected.cacheControl
      || (headers['content-security-policy'] ?? null) !== expected.csp
      || headers['content-length'] !== String(asset?.byteLength ?? -1), 'L3.09-asset-contract');
    fail(cell.method === 'GET' && !sameFile(content, asset), 'L3.09-asset-bytes');
    break;
  }
  case 'asset-missing':
    fail(observation.status !== outcomes.missingAsset.status, 'L3.09-asset-missing-status');
    break;
  case 'api-root':
    fail(observation.status !== outcomes.apiRoot.status || headers['content-type'] !== outcomes.apiRoot.contentType, 'L3.09-api-root');
    break;
  case 'no-leak': {
    // Unreadable content cannot be cleared: no-leak fails closed.
    const text = content?.toString('utf8');
    fail(text === undefined || outcomes.noLeak.forbidden.some(marker => text.includes(marker)), 'L3.09-no-leak');
    break;
  }
  case 'api': {
    const expected = context.listener.apiCases.find(entry => entry.id === cell.caseId);
    fail(!expected || observation.status !== expected.status, 'L3.09-api-status');
    fail(expected?.error !== undefined && jsonError(content) !== expected.error, 'L3.09-api-error');
    fail(expected?.contentTypeOptions !== undefined && headers['x-content-type-options'] !== expected.contentTypeOptions,
      'L3.09-api-headers');
    break;
  }
  case 'csp-report': {
    const expected = context.listener.cspReports.find(entry => entry.id === cell.caseId);
    fail(!expected || observation.status !== expected.status, 'L3.09-csp-report-status');
    break;
  }
  case 'app-api': {
    // 502-503: 200 and Access-Control-Allow-Origin === FA (the bound public origin), exactly.
    const expected = context.app.api;
    fail(cell.caseId !== expected.id || observation.status !== expected.status, 'L3.09-app-api-status');
    fail(headers['access-control-allow-origin'] !== expected.allowOrigin.replace('${FA}', () => context.publicOrigin),
      'L3.09-app-api-headers');
    break;
  }
  case 'app-absent': // 518: the app listener serves no Console file or SPA fallback.
    fail(observation.status !== context.app.staticStatus, 'L3.09-app-absent-status');
    break;
  case 'ruled': // Rulings (see CONSOLE_ORIGIN_RULED).
    switch (cell.ruling) {
    case 'status-only': // b: the status is recorded; the GET body carries only a status.
      fail(cell.method === 'GET' && !statusOnlyBody(content, observation.status), 'L3.09-ruled-status-body');
      break;
    case 'no-credentialed-cors': // c: the status is recorded; no credentialed wildcard, 'null' or reflection.
      fail(!credentialedCorsRefused(headers), 'L3.09-ruled-preflight-cors');
      break;
    case 'exact-404': // d
      fail(observation.status !== outcomes.notFound.status, 'L3.09-ruled-404-status');
      fail(!ruledNotFoundHolds(cell, headers, content, outcomes.notFound), 'L3.09-ruled-404-contract');
      break;
    case '404-or-spa': // e: d's exact 404, or the SPA fallback with the bound document
      if (observation.status === outcomes.notFound.status)
        fail(!ruledNotFoundHolds(cell, headers, content, outcomes.notFound), 'L3.09-ruled-404-or-spa-contract');
      else if (observation.status === outcomes.spa.status) {
        // The ruling's letter only: GET body = the bound non-empty document; HEAD (no body, checked above)
        // Content-Length = its byteLength. Other headers are RECORDED by name in the receipt, never failed.
        fail(cell.method === 'HEAD' && headers['content-length'] !== String(context.document?.byteLength ?? -1),
          'L3.09-ruled-404-or-spa-contract');
        fail(cell.method === 'GET' && !sameFile(content, context.document), 'L3.09-ruled-404-or-spa-document');
      } else failures.push('L3.09-ruled-404-or-spa-status');
      break;
    default: failures.push('L3.09-ruled-unknown');
    }
    break;
  }
  return failures;
}

/**
 * The SAME predicate for local controls and live observations. It rebuilds the plan
 * from canon + binding (a runner cannot shrink it), re-checks all three provenance hashes,
 * rebuilds every cell's exact request bytes from the binding, and reads every body as
 * decoded content (responseContent).
 * FAILED dominates BLOCKED: a missing binding never masks an observed defect.
 */
export function evaluateConsoleOriginMatrix(input: ConsoleOriginRunInput, observations: readonly ConsoleOriginObservation[],
  canon: ConsoleOriginCanon = CANON, listener: ConsoleOriginListenerCanon = LISTENER_CANON,
  app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON): ConsoleOriginVerdict {
  const failures = new Set<string>(), blocked = new Set<string>();
  if (!consoleOriginProvenanceHolds(canon)) failures.add('L3.09-provenance');
  if (!consoleOriginListenerProvenanceHolds(listener)) failures.add('L3.09-listener-provenance');
  if (!consoleOriginAppListenerProvenanceHolds(app)) failures.add('L3.09-app-listener-provenance');
  if (!consoleOriginAssetFormsHold(canon)) failures.add('L3.09-asset-form-provenance');
  const cells = planConsoleOriginMatrix(input.binding, canon, listener, app), record = isRecord(input.binding) ? input.binding : {};
  const byCell = new Map<string, ConsoleOriginObservation[]>();
  for (const observation of observations) byCell.set(observation.cell, [...byCell.get(observation.cell) ?? [], observation]);
  const planned = new Map(cells.map(cell => [cell.id, cell]));
  for (const id of byCell.keys()) if (planned.get(id)?.target == null) failures.add('L3.09-unplanned-cell');
  const assets: CellContext['assets'] = {};
  const boundAssets = isRecord(record.assets) ? record.assets : {};
  for (const definition of ASSET_SLOTS) {
    if (!definition.applicable) continue; // Ruling a: never read from the binding.
    const { asset } = readAsset(boundAssets[definition.slot], definition.valid);
    if (asset) assets[definition.slot] = asset;
  }
  // Unbound (or empty-bound) document: still fallible against ONE common GET body (its decoded
  // content; an unreadable or EMPTY one binds nothing, so every document/SPA body and HEAD length
  // check fails), then capped at BLOCKED.
  const firstSpa = cells.find(cell => cell.kind === 'spa' && cell.method === 'GET' && byCell.get(cell.id)?.length === 1);
  const common = firstSpa ? responseContent(byCell.get(firstSpa.id)![0]).body : null;
  const document = readBoundFile(record.document).file ?? (common && common.length > 0 ? fileOf(common) : null);
  const context: CellContext = { canon, listener, app, publicOrigin: input.publicOrigin, document, assets };
  const results = new Map<string, { failures: string[]; verdict: ConsoleOriginCellReceipt['verdict']; status?: number;
    nonCanonicalHeaders?: string[] }>();
  const pairs = new Map<string, Set<number>>();
  for (const cell of cells) {
    const found = byCell.get(cell.id) ?? [], cellFailures: string[] = [];
    // Ruling a: never sent, never BLOCKED (an answer for it is already L3.09-unplanned-cell).
    if (cell.notApplicable) { results.set(cell.id, { failures: [], verdict: 'not_applicable' }); continue; }
    if (cell.target === null) { results.set(cell.id, { failures: [], verdict: 'blocked' }); continue; }
    if (found.length !== 1) cellFailures.push(found.length === 0 ? 'L3.09-missing-cell' : 'L3.09-duplicate-cell');
    const observation = found[0];
    // Rulings b and c: ALLOW with the status recorded; their own predicates still run.
    const recorded = cell.ruling !== null && RECORDED_RULINGS.includes(cell.ruling);
    // Harness integrity applies to EVERY sent cell: a normalizing client voids the matrix,
    // and so does a runner that drops/changes a header or the body it was planned to send.
    if (observation && !sentTargetIs(observation.written, cell.method, cell.target)) cellFailures.push('L3.09-raw-target');
    if (observation && !sentRequestIs(cell, observation.written, input, listener, app)) cellFailures.push('L3.09-raw-request');
    if (observation) {
      cellFailures.push(...checkCell(cell, observation, context));
      // GET and HEAD of one vector agree (enforced below on 'pass' cells only: a recorded status may differ).
      if (cell.method !== 'OPTIONS') pairs.set(cell.pair, (pairs.get(cell.pair) ?? new Set()).add(observation.status));
    }
    // Ruling e's 200 branch: the canonical SPA header names it did not carry are recorded (names only).
    const spaBranch = !!observation && cell.ruling === '404-or-spa' && observation.status === canon.outcomes.spa.status;
    results.set(cell.id, { failures: cellFailures, verdict: recorded ? 'recorded' : 'pass',
      status: recorded || spaBranch ? observation?.status : undefined,
      nonCanonicalHeaders: spaBranch ? spaHeadersDiffering(observation.headers, canon.outcomes.spa, document) : undefined });
  }
  for (const cell of cells) {
    const result = results.get(cell.id)!;
    if ((pairs.get(cell.pair)?.size ?? 0) > 1 && result.verdict === 'pass') result.failures.push('L3.09-get-head-status');
    for (const failure of result.failures) failures.add(failure);
    if (result.failures.length > 0) result.verdict = 'fail';
    else if (cell.gap && result.verdict !== 'recorded') { result.verdict = 'blocked'; blocked.add(cell.gap); }
  }
  const receipts = cells.map((cell): ConsoleOriginCellReceipt => {
    const { verdict, status, nonCanonicalHeaders } = results.get(cell.id)!;
    if (verdict === 'recorded' && typeof status === 'number') return { cell: cell.id, verdict, status };
    if (verdict === 'pass' && typeof status === 'number' && nonCanonicalHeaders) return { cell: cell.id, verdict, status, nonCanonicalHeaders };
    return { cell: cell.id, verdict };
  });
  return { passed: failures.size === 0 && blocked.size === 0, failures: [...failures], blocked: [...blocked], cells: receipts };
}

// ---- Local fallibility controls (synthetic bytes only; no socket, no network) ----
type FixtureResponse = { status: number; headers: Record<string, string>; body: Buffer };
function encodeResponse(response: FixtureResponse, head: boolean): Buffer {
  const headers = { 'content-length': String(response.body.length), ...response.headers };
  const block = Object.entries(headers).map(([name, value]) => `${name}: ${value}\r\n`).join('');
  return Buffer.concat([Buffer.from(`HTTP/1.1 ${response.status} Fixture\r\n${block}\r\n`, 'latin1'), head ? Buffer.alloc(0) : response.body]);
}
/** Every scalar leaf path of a JSON value (provenance mutants alter each one). */
function leafPaths(value: unknown, path: readonly (string | number)[] = []): (string | number)[][] {
  if (value === null || typeof value !== 'object') return [[...path]];
  return Object.entries(value).flatMap(([key, child]) => leafPaths(child, [...path, Array.isArray(value) ? Number(key) : key]));
}
/**
 * Every control returns the id of a control that did NOT behave; [] means all held.
 * Must run (and return []) before any Console-origin network cell.
 */
export function localConsoleOriginReds(): string[] {
  const broken = new Set<string>();
  const hold = (condition: boolean, control: string) => { if (!condition) broken.add(control); };
  hold(consoleOriginProvenanceHolds(), 'L3.09-local-provenance');
  hold(consoleOriginListenerProvenanceHolds(), 'L3.09-local-listener-provenance');
  hold(consoleOriginAppListenerProvenanceHolds(), 'L3.09-local-app-listener-provenance');
  hold(CANON.absentStaticPaths.length === 20 && CANON.malformedStaticPaths.length === 27 && CANON.filesOnlyStaticPaths.length === 20,
    'L3.09-local-canon-counts');
  hold(LISTENER_CANON.apiCases.length === 5 && LISTENER_CANON.cspReports.length === 2, 'L3.09-local-listener-counts');
  hold(APP_LISTENER_CANON.staticRoutes.length === 7 && APP_LISTENER_CANON.staticMethods.length === 2, 'L3.09-local-app-listener-counts');
  // 500 presents the same literal (non-template) bearer as 449: one browserToken slot serves both.
  hold(APP_LISTENER_CANON.api.authorization === LISTENER_CANON.apiCases.find(apiCase => apiCase.id === APP_CREDENTIAL_CASE)?.authorization
    && !APP_LISTENER_CANON.api.authorization.includes('${')
    && CREDENTIAL_SLOTS.some(slot => slot.caseId === APP_CREDENTIAL_CASE && slot.kind === 'browser'), 'L3.09-local-app-credential-form');
  hold(consoleOriginAssetFormsHold(), 'L3.09-local-asset-forms');
  // The J00 slot mapping matches the source bearers: only browser-token is a literal, non-template bearer.
  hold(LISTENER_CANON.apiCases.length === CREDENTIAL_SLOTS.length && LISTENER_CANON.apiCases.every(apiCase =>
    CREDENTIAL_SLOTS.some(slot => slot.caseId === apiCase.id && (slot.kind === 'browser') === !apiCase.authorization.includes('${')))
    && LISTENER_CANON.apiCases.every(apiCase => apiCase.authorization.startsWith('Bearer ')), 'L3.09-local-credential-forms');
  for (const bad of [undefined, null, {}, { url: 7 }, { url: '' }, { url: 'http://127.0.0.1:3032/' },
    { url: 'http://127.0.0.1:3032/omega/console/' }, { url: 'http://u:p@127.0.0.1:3032' }, { url: 'ftp://127.0.0.1:3032' }])
    hold(consoleOriginBindingUrl(bad) === null, 'L3.09-local-binding-url-refused');
  hold(consoleOriginBindingUrl({ url: 'http://127.0.0.1:3032' }) === 'http://127.0.0.1:3032', 'L3.09-local-binding-url-restored');
  // Raw parser and request-line controls (the live path parses with these).
  const notFound = Buffer.from('HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\n{}', 'latin1');
  hold(parseRawResponse(notFound, false, true)?.body.toString() === '{}', 'L3.09-local-parse-length');
  hold(parseRawResponse(notFound, true, true)?.bytesAfterHeaders === 2, 'L3.09-local-parse-head-body');
  const chunked = Buffer.from('HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n', 'latin1');
  hold(parseRawResponse(chunked, false, false)?.body.toString() === 'hello' && parseRawResponse(chunked, false, false)?.complete === true,
    'L3.09-local-parse-chunked');
  hold(parseRawResponse(chunked.subarray(0, chunked.length - 4), false, false)?.complete === false, 'L3.09-local-parse-incomplete');
  hold(parseRawResponse(Buffer.from('HTTP/1.1 200 OK\r\n', 'latin1'), false, true) === null
    && parseRawResponse(Buffer.from('garbage\r\n\r\n', 'latin1'), false, true) === null, 'L3.09-local-parse-refused');
  // 1xx interim blocks: skipped, never the answer; the body/HEAD bytes are the FINAL response's.
  const proceed = Buffer.from('HTTP/1.1 100 Continue\r\n\r\n', 'latin1');
  const hints = Buffer.from('HTTP/1.1 103 Early Hints\r\nLink: </omega/console/assets/x.js>; rel=preload\r\n\r\n', 'latin1');
  const switching = Buffer.from('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n', 'latin1');
  const afterInterim = parseRawResponse(Buffer.concat([proceed, hints, notFound]), false, false);
  hold(afterInterim?.status === 404 && afterInterim.body.toString() === '{}' && afterInterim.complete && afterInterim.interim === 2,
    'L3.09-local-parse-interim');
  hold(parseRawResponse(hints, false, false) === null && parseRawResponse(hints, true, true) === null, 'L3.09-local-parse-interim-pending');
  hold(parseRawResponse(Buffer.concat([hints, notFound]), true, true)?.bytesAfterHeaders === 2, 'L3.09-local-parse-interim-head-body');
  hold(parseRawResponse(Buffer.concat([switching, notFound]), false, false)?.status === 101, 'L3.09-local-parse-switch');
  // Field names are RFC 9110 5.6.2 tokens with no whitespace before the colon
  // (RFC 9112 5.1): any other line makes the whole response unparseable (null => a transport failure),
  // never a header read under another name. The full token alphabet itself parses.
  const okHead = 'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n';
  for (const line of ['X-Bad : 1', 'X-Bad\t: 1', ' X-Bad: 1', '\tX-Bad: 1', 'X Bad: 1', 'X(Bad): 1', 'X"Bad": 1', 'X/Bad: 1',
    'X@Bad: 1', 'X{Bad}: 1', 'X[Bad]: 1', 'X,Bad: 1', 'X;Bad: 1', 'X=Bad: 1', 'X\u00e4: 1', 'X\u0000: 1', 'X\u007f: 1', ': 1'])
    hold(parseRawResponse(Buffer.from(`${okHead}${line}\r\n\r\n`, 'latin1'), false, true) === null, 'L3.09-local-parse-field-name-refused');
  const tokenName = "X-Ok_1.~!#$%&'*+^`|";
  hold(parseRawResponse(Buffer.from(`${okHead}${tokenName}: 1\r\n\r\n`, 'latin1'), false, true)?.headers[tokenName.toLowerCase()] === '1',
    'L3.09-local-parse-field-name-token');
  // Repeated field lines fold in order with ', ' (RFC 9110 5.3), names case-insensitive, each value OWS-trimmed.
  const folded = parseRawResponse(Buffer.from(`${okHead}Cache-Control: no-cache\r\ncache-control: no-store\r\nCACHE-CONTROL:\tprivate \r\n\r\n`,
    'latin1'), false, true);
  hold(folded?.headers['cache-control'] === 'no-cache, no-store, private' && folded.headers['content-length'] === '0', 'L3.09-local-parse-fold');
  // Strict raw JSON (rulings b, d): exact member lists; a repeated member at any depth, an escape, a number, null, an
  // array, a non-ASCII or control byte, nesting beyond maxDepth or anything after the value refuses.
  const strict = (text: string | Buffer, depth = 2) => parseStrictJson(Buffer.from(text), depth);
  const parsedStrict = strict(' {"a" : true, "b":{"c":"d e"}}\n');
  hold(JSON.stringify(parsedStrict) === JSON.stringify({ kind: 'object', members: [{ key: 'a', value: { kind: 'boolean', value: true } },
    { key: 'b', value: { kind: 'object', members: [{ key: 'c', value: { kind: 'string', text: 'd e', start: 24, end: 27 } }] } }] })
    && strict('{}')?.kind === 'object' && strict('"x"')?.kind === 'string' && strict('{"a":{}}', 2)?.kind === 'object'
    && strict('{"a":true} ')?.kind === 'object', 'L3.09-local-strict-json');
  for (const [text, depth] of [['{"a":true,"a":true}', 2], ['{"b":{"a":"x","a":"x"}}', 2], ['{"a":"\\u0061"}', 2], ['{"a":"\\\""}', 2],
    ['{"a":1}', 2], ['{"a":null}', 2], ['{"a":["x"]}', 2], ['{"a":-1}', 2], ['{"a":{}}', 1], ['{"a":{"b":{}}}', 2],
    ['{"a":true}x', 2], ['{"a":true}{"a":true}', 2], ['{"a":true,}', 2], ['{"a" true}', 2], ['{a:true}', 2], ["{'a':true}", 2], ['{"a":tru}', 2],
    ['{"a":truex}', 2], ['{"a":"\u00e9"}', 2], ['{"a":"\t"}', 2], ['{"a":"x', 2], ['', 2], ['{', 2], [Buffer.from([0xef, 0xbb, 0xbf, 0x7b, 0x7d]), 2]] as const)
    hold(strict(text, depth) === null, 'L3.09-local-strict-json-refused');
  const literal = buildRawRequest('GET', '/omega/console/../foo', '127.0.0.1:9', '*/*');
  hold(sentTargetIs(literal, 'GET', '/omega/console/../foo') && !sentTargetIs(literal, 'GET', '/omega/foo')
    && !sentTargetIs(buildRawRequest('GET', '/omega/foo', '127.0.0.1:9', '*/*'), 'GET', '/omega/console/../foo'), 'L3.09-local-raw-line');
  hold(literal.equals(Buffer.from('GET /omega/console/../foo HTTP/1.1\r\nHost: 127.0.0.1:9\r\nAccept: */*\r\n'
    + 'Accept-Encoding: identity\r\nConnection: close\r\n\r\n', 'latin1')), 'L3.09-local-raw-bytes');
  hold(buildRawRequest('POST', '/__omega/csp-report', '127.0.0.1:9', '*/*', [['Content-Type', FETCH_STRING_BODY_TYPE]], Buffer.from('xx'))
    .equals(Buffer.from('POST /__omega/csp-report HTTP/1.1\r\nHost: 127.0.0.1:9\r\nAccept: */*\r\nAccept-Encoding: identity\r\n'
      + 'Content-Type: text/plain;charset=UTF-8\r\nContent-Length: 2\r\nConnection: close\r\n\r\nxx', 'latin1')), 'L3.09-local-raw-post');
  // The identity request cannot be overridden or doubled by an extra header.
  for (const name of ['Accept-Encoding', 'accept-encoding', 'Host', 'Content-Length', 'Transfer-Encoding']) {
    let reserved = false;
    try { buildRawRequest('GET', '/x', '127.0.0.1:9', '*/*', [[name, 'gzip']]); } catch { reserved = true; }
    hold(reserved, 'L3.09-local-reserved-header-refused');
  }
  let refused = false;
  try { buildRawRequest('GET', '/omega/console/naïve plan', '127.0.0.1:9', '*/*'); } catch { refused = true; }
  hold(refused, 'L3.09-local-unsendable-refused');
  let injected = false;
  try { buildRawRequest('GET', '/x', '127.0.0.1:9', '*/*', [['Origin', 'http://a\r\nX-Injected: 1']]); } catch { injected = true; }
  hold(injected, 'L3.09-local-header-injection-refused');
  // Content decoding: every supported coding round-trips; anything else is unreadable.
  const plain = Buffer.from('outside the build\nroot:x:0:0:root:/root:/bin/sh');
  const decodes = (encoding: string | undefined, bytes: Buffer, transfer?: string) => {
    const headers: Record<string, string> = {};
    if (encoding !== undefined) headers['content-encoding'] = encoding;
    if (transfer !== undefined) headers['transfer-encoding'] = transfer;
    return responseContent({ body: bytes, headers });
  };
  const same = (result: { body: Buffer | null; coded: boolean }, coded: boolean) => result.coded === coded && !!result.body?.equals(plain);
  hold(same(decodes(undefined, plain), false) && same(decodes('identity', plain), false) && same(decodes(' Identity ', plain), false)
    && same(decodes(undefined, plain, 'chunked'), false), 'L3.09-local-content-identity');
  hold(same(decodes('gzip', gzipSync(plain)), true) && same(decodes('x-gzip', gzipSync(plain)), true)
    && same(decodes('GZIP', gzipSync(plain)), true) && same(decodes('br', brotliCompressSync(plain)), true)
    && same(decodes('deflate', deflateSync(plain)), true) && same(decodes('deflate', deflateRawSync(plain)), true)
    && same(decodes('gzip, br', brotliCompressSync(gzipSync(plain))), true)
    && same(decodes('identity, gzip', gzipSync(plain)), true), 'L3.09-local-content-decode');
  for (const [encoding, bytes, transfer] of [['compress', plain, undefined], ['zstd', gzipSync(plain), undefined], ['', plain, undefined],
    ['gzip', plain, undefined], ['br', gzipSync(plain), undefined], ['gzip, zstd', gzipSync(plain), undefined],
    [undefined, gzipSync(plain), 'gzip, chunked'], [undefined, plain, 'identity']] as const)
    hold(decodes(encoding, bytes, transfer).coded && decodes(encoding, bytes, transfer).body === null, 'L3.09-local-content-unreadable');
  const bomb = gzipSync(Buffer.alloc(CONTENT_DECODE_LIMIT + 1));
  hold(decodes('gzip', bomb).coded && decodes('gzip', bomb).body === null, 'L3.09-local-content-bounded');

  // Synthetic restored matrix: canonical outcomes over fixture-only bytes.
  const publicOrigin = 'http://localhost:3031', reportPath = LISTENER_CANON.cspReportPath;
  const documentBytes = Buffer.from('<!doctype html><title>fixture</title>');
  const assetBytes: Record<ConsoleOriginAssetSlot, Buffer> = { 'immutable-js': Buffer.from('fixture-js'),
    'utf8-space-name': Buffer.from('fixture utf8'), 'percent-space-name': Buffer.from('fixture percent') };
  const [jsName, utf8Name, percentName] = CANON.fixtureAssetNames;
  // Synthetic, form-valid fixture strings only: NOT credentials.
  const credentials = { browserToken: 'fixture-browser-token', assertions: {
    assertion: 'omega_delegate_fixture.assertion.a1', 'foreign-origin': 'omega_delegate_fixture.foreign.b2',
    'fa-origin': 'omega_delegate_fixture.fa.c3', operator: 'omega_delegate_fixture.operator.d4' } };
  const appListenerUrl = 'http://127.0.0.1:7';
  const binding: ConsoleOriginBinding = { url: 'http://127.0.0.1:9', appListenerUrl, document: fileOf(documentBytes), assets: {
    'immutable-js': { name: jsName, ...fileOf(assetBytes['immutable-js']) } }, credentials };
  const host = '127.0.0.1:9';
  const runInput = (bound: unknown = binding): ConsoleOriginRunInput => ({ binding: bound, publicOrigin });
  const appHost = '127.0.0.1:7';
  const cspWith = (report: string) => CANON.documentCspDirectives.map(directive => directive.replace('${FA}', publicOrigin)
    .replace('${CSP_REPORT_PATH}', report)).join('; ');
  const reportOnlyWith = (report: string) => CANON.outcomes.documentHeaders.reportOnly.replace('${CSP_REPORT_PATH}', report);
  const csp = cspWith(reportPath);
  const outcomes = CANON.outcomes;
  // The gateway's exact not-found bytes (server.mjs:201): respond() appends the 32-hex trace id as requestId,
  // which the 46e39a4686 contract requires (286-292). A fixture id, not a real trace id.
  const notFoundId = '0123456789abcdef0123456789abcdef';
  const json404 = (): FixtureResponse => ({ status: 404, headers: { 'content-type': outcomes.notFound.contentType,
    'cache-control': outcomes.notFound.cacheControl, 'x-content-type-options': outcomes.notFound.contentTypeOptions },
  body: Buffer.from(JSON.stringify({ error: outcomes.notFound.error, requestId: notFoundId })) });
  const spaDocument = (): FixtureResponse => ({ status: 200, headers: { 'content-type': outcomes.spa.contentType,
    'cache-control': outcomes.spa.cacheControl, 'content-security-policy': csp }, body: documentBytes });
  const restoredResponse = (cell: ConsoleOriginCell): FixtureResponse => {
    switch (cell.kind) {
    case 'absent': case 'malformed': case 'files-only': case 'asset-missing': case 'no-leak': return json404();
    case 'spa': case 'spa-dotted': return spaDocument();
    case 'document': return { status: 200, headers: { 'content-type': outcomes.spa.contentType, 'content-security-policy': csp,
      'content-security-policy-report-only': reportOnlyWith(reportPath),
      'x-content-type-options': 'nosniff', 'referrer-policy': outcomes.documentHeaders.referrerPolicy,
      'cache-control': outcomes.documentHeaders.cacheControl }, body: documentBytes };
    case 'asset': return { status: 200, headers: { 'content-type': outcomes.asset.contentType,
      'cache-control': outcomes.asset.cacheControl }, body: assetBytes['immutable-js'] };
    // Ruling a: never sent (kept so every kind has a fixture).
    case 'asset-encoded': return { status: 200, headers: { 'content-type': 'text/plain; charset=utf-8',
      'cache-control': outcomes.encodedAsset.cacheControl }, body: assetBytes[cell.slot!] };
    case 'api-root': return { status: 403, headers: { 'content-type': outcomes.apiRoot.contentType }, body: Buffer.from('{"error":"x"}') };
    case 'api': {
      const apiCase = LISTENER_CANON.apiCases.find(entry => entry.id === cell.caseId)!;
      return { status: apiCase.status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store',
        'x-content-type-options': 'nosniff' }, body: Buffer.from(JSON.stringify(apiCase.error ? { error: apiCase.error } : { ok: true })) };
    }
    case 'csp-report': return { status: 204, headers: { 'x-content-type-options': 'nosniff' }, body: Buffer.alloc(0) };
    case 'app-api': return { status: 200, headers: { 'content-type': 'application/json; charset=utf-8',
      'access-control-allow-origin': publicOrigin, vary: 'Origin' }, body: Buffer.from('{"sandboxes":[]}') };
    case 'app-absent': return json404();
    case 'ruled':
      // b: the gateway's own probe bodies (server.mjs:255, lifecycle.mjs:60); c: the Console listener's 204 (server.mjs:226-228).
      if (cell.ruling === 'status-only') return { status: 200, headers: { 'content-type': 'application/json; charset=utf-8',
        'cache-control': 'no-store' }, body: Buffer.from(cell.ruledId === 'healthz' ? '{"live":true}' : '{"ready":true,"checks":{"ee":"ok","exchange":"ok"}}') };
      if (cell.ruling === 'no-credentialed-cors') return { status: 204, headers: { 'x-content-type-options': 'nosniff' }, body: Buffer.alloc(0) };
      return json404(); // d, and e's 404 branch
    }
  };
  const exchangeOf = (written: Buffer, received: Buffer): RawExchange => ({ written, received, closed: true, transportError: false });
  const observe = (cell: ConsoleOriginCell, response: FixtureResponse, target = cell.target!, extra = Buffer.alloc(0),
    bound: unknown = binding) => observeExchange(cell, exchangeOf(buildCellRequest(cell, runInput(bound), LISTENER_CANON, target),
    Buffer.concat([encodeResponse(response, cell.method === 'HEAD'), extra])));
  const plan = planConsoleOriginMatrix(binding);
  const sent = plan.filter(cell => cell.target !== null);
  // Every planned cell is sent except the 4 not-applicable ones (ruling a).
  // 293 at 24a61e0424 + 80 files-only cells (20 vectors x GET/HEAD x 2 accepts) - the 8 ruled cells now planned
  // ONCE as their canonical files-only cell (dotfile, assets-directory: 2 spellings x GET/HEAD x 2 accepts).
  hold(sent.length === plan.length - 4 && plan.length === new Set(plan.map(cell => cell.id)).size && plan.length === 365,
    'L3.09-local-plan-complete');
  hold(plan.filter(cell => cell.kind === 'absent' || cell.kind === 'malformed').length === 47 * 4
    && plan.filter(cell => cell.kind === 'files-only').length === 20 * 4 && plan.filter(cell => cell.kind === 'ruled').length === 34
    && plan.filter(cell => cell.kind === 'spa').length === 16 && plan.filter(cell => cell.kind === 'asset').length === 4
    && plan.filter(cell => cell.kind === 'asset-encoded').length === 4 && plan.filter(cell => cell.kind === 'document').length === 4
    && plan.filter(cell => cell.kind === 'api').length === 5 && plan.filter(cell => cell.kind === 'csp-report' && cell.method === 'POST'
      && cell.target === '/__omega/csp-report').length === 2
    && plan.filter(cell => cell.kind === 'app-api' && cell.method === 'GET' && cell.target === '/omega/api/workspace/sandboxes').length === 1
    && plan.filter(cell => cell.kind === 'app-absent' && cell.accept === 'text/html').length === 14, 'L3.09-local-plan-shape');
  // Every later control looks cells up by kind/case: a short plan is reported, never a TypeError.
  if (broken.has('L3.09-local-plan-complete') || broken.has('L3.09-local-plan-shape')) return [...broken];
  // Socket choice: app-listener cells go to appListenerUrl (498 `base`), every other cell to url.
  hold(plan.every(cell => consoleOriginCellOrigin(cell, binding) === (APP_KINDS.includes(cell.kind) ? appListenerUrl : binding.url))
    && plan.filter(cell => APP_KINDS.includes(cell.kind)).every(cell => buildCellRequest(cell, runInput()).toString('latin1')
      .includes(`\r\nHost: ${appHost}\r\n`)), 'L3.09-local-app-listener-routing');
  // Every planned request asks for identity content exactly once.
  hold(sent.every(cell => buildCellRequest(cell, runInput()).toString('latin1').split('\r\nAccept-Encoding: identity\r\n').length === 2),
    'L3.09-local-accept-identity');
  const restored =sent.map(cell => observe(cell, restoredResponse(cell)));
  const verdict = (observations: readonly ConsoleOriginObservation[], bound: unknown = binding, canon: ConsoleOriginCanon = CANON,
    listener: ConsoleOriginListenerCanon = LISTENER_CANON, app: ConsoleOriginAppListenerCanon = APP_LISTENER_CANON) =>
    evaluateConsoleOriginMatrix(runInput(bound), observations, canon, listener, app);
  const red = (observations: readonly ConsoleOriginObservation[], check: string, bound: unknown = binding) => {
    const result = verdict(observations, bound);
    return !result.passed && result.failures.includes(check);
  };
  const swap = (id: string, next: ConsoleOriginObservation) => restored.map(row => row.cell === id ? next : row);
  const restoredVerdict = verdict(restored);
  hold(restoredVerdict.passed && restoredVerdict.failures.length === 0 && restoredVerdict.blocked.length === 0
    && restoredVerdict.cells.every(cell => ['pass', 'recorded', 'not_applicable'].includes(cell.verdict)), 'L3.09-local-restored');

  const exact404 = sent.filter(cell => ['absent', 'malformed', 'files-only', 'asset-missing', 'app-absent'].includes(cell.kind));
  hold(exact404.filter(cell => cell.kind === 'app-absent').length === 14, 'L3.09-local-app-absent-cells');
  for (const cell of exact404) {
    const check = cell.kind === 'asset-missing' ? 'L3.09-asset-missing-status' : cell.kind === 'app-absent' ? 'L3.09-app-absent-status'
      : 'L3.09-not-found-status';
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), status: 200 })), check), 'L3.09-local-red-200');
    if (cell.kind !== 'asset-missing')
      hold(red(swap(cell.id, observe(cell, spaDocument())), check), 'L3.09-local-red-spa-leak');
  }
  hold(exact404.some(cell => cell.target === '/omega/consolex'), 'L3.09-local-red-spa-leak-consolex');
  const heads = sent.filter(cell => cell.method === 'HEAD');
  hold(red(restored.filter(row => !row.cell.includes('|HEAD|')), 'L3.09-missing-cell'), 'L3.09-local-red-head-dropped');
  for (const cell of heads) {
    hold(red(restored.filter(row => row.cell !== cell.id), 'L3.09-missing-cell'), 'L3.09-local-red-head-dropped');
    hold(red(swap(cell.id, observe(cell, restoredResponse(cell), cell.target!, Buffer.from('body'))), 'L3.09-head-body'),
      'L3.09-local-red-head-body');
    if (cell.ruling !== null && RECORDED_RULINGS.includes(cell.ruling)) continue; // status recorded only (ruling b)
    const status = restoredResponse(cell).status === 404 ? 200 : 404;
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), status })), 'L3.09-get-head-status'),
      'L3.09-local-red-get-head-disagree');
  }
  for (const pair of new Set(sent.map(cell => cell.pair.split('|')[0])))
    hold(red(restored.filter(row => !row.cell.startsWith(`${pair}|`)), 'L3.09-missing-cell'), 'L3.09-local-red-vector-omitted');
  const arrays = Object.entries(CANON).filter((entry): entry is [string, string[]] => Array.isArray(entry[1]));
  for (const [field, values] of arrays) for (let index = 0; index < values.length; index++) {
    const omitted = JSON.parse(JSON.stringify(CANON)) as Record<string, unknown>, altered = JSON.parse(JSON.stringify(CANON)) as Record<string, unknown>;
    omitted[field] = values.filter((_, at) => at !== index);
    altered[field] = values.map((value, at) => at === index ? `${value}/` : value);
    for (const canon of [omitted, altered]) {
      const result = verdict(restored, binding, canon as unknown as ConsoleOriginCanon);
      hold(!result.passed && result.failures.includes('L3.09-provenance'), 'L3.09-local-red-canon-altered');
    }
  }
  // Rebind 46e39a4686: ANY copied vector or expected outcome altered or omitted is red, at ANY depth (the loop above
  // covers top-level arrays only): every scalar leaf of CANON altered (string + '/', number + 1, null => 'altered'),
  // and every element of every array at any depth omitted (filesOnlyStaticPaths, outcomes.notFound.keys and
  // outcomes.noLeak.forbidden included). Each variant must fail L3.09-provenance; a throw is a broken control.
  const cloneCanon = () => JSON.parse(JSON.stringify(CANON)) as Record<string | number, unknown>;
  const nodeAt = (root: Record<string | number, unknown>, path: readonly (string | number)[]) =>
    path.reduce<Record<string | number, unknown>>((node, key) => node[key] as Record<string | number, unknown>, root);
  const canonVariants: Record<string | number, unknown>[] = [];
  for (const path of leafPaths(CANON)) {
    const altered = cloneCanon(), parent = nodeAt(altered, path.slice(0, -1)), leaf = path[path.length - 1], old = parent[leaf];
    parent[leaf] = typeof old === 'number' ? old + 1 : typeof old === 'string' ? `${old}/` : 'altered';
    canonVariants.push(altered);
  }
  const arrayPaths = (value: unknown, path: (string | number)[] = []): (string | number)[][] =>
    value === null || typeof value !== 'object' ? [] : [...(Array.isArray(value) ? [path] : []),
      ...Object.entries(value).flatMap(([key, child]) => arrayPaths(child, [...path, Array.isArray(value) ? Number(key) : key]))];
  for (const path of arrayPaths(CANON)) (nodeAt(cloneCanon(), path) as unknown as unknown[]).forEach((_, index) => {
    const omitted = cloneCanon();
    (nodeAt(omitted, path) as unknown as unknown[]).splice(index, 1);
    canonVariants.push(omitted);
  });
  // 138 scalar leaves + 104 array elements at 46e39a4686 (every one of them a copied vector or outcome).
  hold(canonVariants.length === leafPaths(CANON).length + 104 && leafPaths(CANON).length === 138, 'L3.09-local-canon-variants');
  for (const canon of canonVariants) {
    let result: ConsoleOriginVerdict | null = null;
    try { result = verdict(restored, binding, canon as unknown as ConsoleOriginCanon); } catch { result = null; }
    hold(!!result && !result.passed && result.failures.includes('L3.09-provenance'), 'L3.09-local-red-canon-leaf-altered');
  }
  // Listener copy: every leaf altered and every case omitted => provenance red.
  const listenerVariants: unknown[] = [];
  for (const path of leafPaths(LISTENER_CANON)) {
    const altered = JSON.parse(JSON.stringify(LISTENER_CANON)) as Record<string | number, unknown>;
    let node = altered;
    for (const key of path.slice(0, -1)) node = node[key] as Record<string | number, unknown>;
    const leaf = path[path.length - 1], old = node[leaf];
    node[leaf] = typeof old === 'number' ? old + 1 : typeof old === 'string' ? `${old}/` : 'altered';
    listenerVariants.push(altered);
  }
  for (const field of ['apiCases', 'cspReports'] as const) LISTENER_CANON[field].forEach((_, index) => {
    const omitted = JSON.parse(JSON.stringify(LISTENER_CANON)) as Record<string, unknown[]>;
    omitted[field] = omitted[field].filter((__, at) => at !== index);
    listenerVariants.push(omitted);
  });
  hold(listenerVariants.length >= 40, 'L3.09-local-listener-variants');
  for (const listener of listenerVariants) {
    const result = verdict(restored, binding, CANON, listener as ConsoleOriginListenerCanon);
    hold(!result.passed && result.failures.includes('L3.09-listener-provenance'), 'L3.09-local-red-listener-altered');
  }
  // App-listener copy: every leaf altered and every static route/method omitted => provenance red.
  const appVariants: unknown[] = leafPaths(APP_LISTENER_CANON).map(path => {
    const altered = JSON.parse(JSON.stringify(APP_LISTENER_CANON)) as Record<string | number, unknown>;
    let node = altered;
    for (const key of path.slice(0, -1)) node = node[key] as Record<string | number, unknown>;
    const leaf = path[path.length - 1], old = node[leaf];
    node[leaf] = typeof old === 'number' ? old + 1 : typeof old === 'string' ? `${old}/` : 'altered';
    return altered;
  });
  for (const field of ['staticRoutes', 'staticMethods'] as const) APP_LISTENER_CANON[field].forEach((_, index) => {
    const omitted = JSON.parse(JSON.stringify(APP_LISTENER_CANON)) as Record<string, unknown[]>;
    omitted[field] = omitted[field].filter((__, at) => at !== index);
    appVariants.push(omitted);
  });
  hold(appVariants.length >= 25, 'L3.09-local-app-listener-variants');
  for (const app of appVariants) {
    const result = verdict(restored, binding, CANON, LISTENER_CANON, app as ConsoleOriginAppListenerCanon);
    hold(!result.passed && result.failures.includes('L3.09-app-listener-provenance'), 'L3.09-local-red-app-listener-altered');
  }
  const positiveKinds: Kind[] = ['spa', 'spa-dotted', 'document', 'asset', 'api', 'csp-report', 'app-api'];
  for (const cell of sent.filter(row => positiveKinds.includes(row.kind))) {
    hold(red(restored.filter(row => row.cell !== cell.id), 'L3.09-missing-cell'), 'L3.09-local-red-positive-missing');
    const result = verdict(swap(cell.id, observe(cell, json404())));
    hold(!result.passed && result.failures.some(check => check.endsWith('-status')), 'L3.09-local-red-positive-404');
    // Same bytes and headers, status only: the kind's own status predicate must fire.
    const statusCheck = cell.kind === 'document' ? 'L3.09-document-status' : cell.kind.startsWith('asset') ? 'L3.09-asset-status'
      : cell.kind === 'api' ? 'L3.09-api-status' : cell.kind === 'csp-report' ? 'L3.09-csp-report-status'
      : cell.kind === 'app-api' ? 'L3.09-app-api-status' : 'L3.09-spa-status';
    const wrong = cell.kind === 'csp-report' ? 200 : 404;
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), status: wrong })), statusCheck), 'L3.09-local-red-positive-status');
  }
  let normalizing = 0;
  for (const cell of sent) {
    hold(red(swap(cell.id, observe(cell, restoredResponse(cell), `${cell.target}x`)), 'L3.09-raw-target'), 'L3.09-local-red-raw-target');
    const url = new URL(cell.target!, 'http://normalized.invalid'), normalized = `${url.pathname}${url.search}`;
    if (normalized === cell.target) continue;
    normalizing++;
    hold(red(swap(cell.id, observe(cell, restoredResponse(cell), normalized)), 'L3.09-raw-target'), 'L3.09-local-red-normalized');
  }
  hold(normalizing >= 10, 'L3.09-local-normalizing-vectors');
  const first = (kind: Kind, method: ConsoleOriginMethod = 'GET') => sent.find(cell => cell.kind === kind && cell.method === method)!;
  const byCase = (caseId: string) => sent.find(cell => cell.caseId === caseId)!;
  const mutate = (cell: ConsoleOriginCell, change: (response: FixtureResponse) => FixtureResponse, check: string) =>
    hold(red(swap(cell.id, observe(cell, change(restoredResponse(cell)))), check), `L3.09-local-red-${check.slice(6)}`);
  const header = (name: string, value?: string) => (response: FixtureResponse) => {
    const headers = { ...response.headers };
    if (value === undefined) delete headers[name]; else headers[name] = value;
    return { ...response, headers };
  };
  mutate(first('absent'), response => ({ ...response, body: Buffer.from('{"error":"other"}') }), 'L3.09-not-found-contract');
  mutate(first('malformed'), header('content-type', 'text/html; charset=utf-8'), 'L3.09-not-found-contract');
  mutate(first('absent', 'HEAD'), header('cache-control', 'no-cache'), 'L3.09-not-found-contract');
  mutate(first('malformed'), header('x-content-type-options'), 'L3.09-not-found-contract');
  mutate(first('spa'), header('cache-control', outcomes.asset.cacheControl), 'L3.09-spa-contract');
  mutate(first('spa', 'HEAD'), header('content-security-policy'), 'L3.09-spa-contract');
  mutate(first('spa', 'HEAD'), header('content-length', String(documentBytes.length + 1)), 'L3.09-spa-contract');
  mutate(first('spa'), response => ({ ...response, body: Buffer.from('<!doctype html><title>other</title>') }), 'L3.09-spa-document');
  mutate(first('spa-dotted'), response => ({ ...response, body: Buffer.from('other') }), 'L3.09-spa-document');
  mutate(first('document'), header('content-security-policy', csp.replace(`frame-ancestors ${publicOrigin}`, 'frame-ancestors *')), 'L3.09-document-csp');
  mutate(first('document'), header('content-security-policy', csp.replace(`; report-uri ${reportPath}`, '')), 'L3.09-document-csp');
  mutate(first('document'), header('content-security-policy-report-only', "require-trusted-types-for 'script'; report-uri /other"), 'L3.09-document-csp');
  mutate(first('document'), header('content-security-policy', `REPORT-URI https://unrelated.example/collect; ${csp}`), 'L3.09-document-csp');
  // (2) CSP3 directive parsing: ';' with or without whitespace, any name case, every
  // duplicate, a second policy and report-to. Each mutant on EVERY document at once, report-only canonical.
  const unrelated = 'https://unrelated.example/collect';
  const documentCells = sent.filter(cell => cell.kind === 'document');
  const withPolicy = (policy: string) => restored.map(row => {
    const cell = documentCells.find(entry => entry.id === row.cell);
    return cell ? observe(cell, header('content-security-policy', policy)(restoredResponse(cell))) : row;
  });
  for (const policy of [
    `style-src 'self';report-uri ${unrelated}; ${csp}`, // first reported shape, verbatim
    `style-src 'self';REPORT-URI ${unrelated}; ${csp}`, // second reported shape, verbatim
    `report-uri ${unrelated};${csp}`, // no-space duplicate, first
    `${csp};report-uri ${unrelated}`, // no-space duplicate, last
    `${csp};REPORT-URI ${unrelated}`,
    `style-src 'self';\treport-uri ${unrelated}; ${csp}`, // HTAB after ';'
    `style-src 'self' ;Report-Uri ${unrelated} ; ${csp}`,
    `${csp}, report-uri ${unrelated}`, // a second enforced policy (also two joined header lines)
    // These three keep the canonical literal check (119) green: only the CSP3 rules catch them.
    `${csp}; , report-uri ${unrelated}`, `${csp}; , default-src 'none'`, // ',' => second policy
    `${csp}; script-src https://evil.example`, `${csp}; Report-Uri ${unrelated}`, // later duplicates
    `${csp}; report-to csp-endpoint`, `REPORT-TO csp-endpoint;${csp}`, // report-to supersedes report-uri
    `script-src https://evil.example;${csp}`, `DEFAULT-SRC https://evil.example; ${csp}`, // CSP3 first-wins duplicates
    `frame-ancestors *;${csp}`, `${csp};object-src https://evil.example`,
    csp.replace(`report-uri ${reportPath}`, `report-uri ${reportPath} ${unrelated}`), // two destinations in one directive
  ]) hold(red(withPolicy(policy), 'L3.09-document-csp'), 'L3.09-local-red-csp3-directives');
  // Header OWS is SP/HTAB only (RFC 9110 5.6.3). A non-ASCII byte such as 0xA0 next to
  // the value must survive parsing, so the CSP3 checks see it and refuse (the browser would ignore that directive).
  // encodeResponse writes latin1, so U+2028 is spelled as its UTF-8 bytes E2 80 A8 ('\u00e2\u0080\u00a8'): a
  // literal '\u2028' would go out as the single byte 0x28 '('. VT and FF are trimmed by String.trim
  // but are not OWS, so like 0xA0 they pin owsTrim.
  for (const policy of [`${csp}\u00a0`, `\u00a0${csp}`, `${csp}\u00a0; report-uri ${unrelated}`, `${csp}\u00e2\u0080\u00a8`,
    `${csp}\u000b`, `\u000c${csp}`])
    hold(red(withPolicy(policy), 'L3.09-document-csp'), 'L3.09-local-red-csp-non-ows');
  // A malformed field name (whitespace before ':' or a non-token character) on a real answer makes the
  // exchange a transport/parse failure; it is never read as a missing or renamed header.
  const renamed = (name: string, to: string) => (response: FixtureResponse) => {
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries(response.headers)) headers[key === name ? to : key] = value;
    return { ...response, headers };
  };
  for (const [cell, change] of [[first('document'), renamed('content-security-policy', 'content-security-policy ')],
    [first('document'), renamed('content-security-policy', 'content-security-policy\t')],
    [first('absent'), renamed('cache-control', 'cache-control ')], [first('spa', 'HEAD'), header('x(y)', '1')],
    [first('absent'), header('x y', '1')], [byCase('csp-report'), header('x@y', '1')]] as const)
    hold(red(swap(cell.id, observe(cell, change(restoredResponse(cell)))), 'L3.09-transport'), 'L3.09-local-red-field-name');
  // Repeated field lines are evaluated JOINED (', '), never first-wins or last-wins. encodeResponse
  // takes a Record, so these answers are written line by line.
  const encodeLines = (status: number, lines: readonly RawHeader[], body: Buffer, head: boolean) => Buffer.concat([Buffer.from(
    `HTTP/1.1 ${status} Fixture\r\n${lines.map(([name, value]) => `${name}: ${value}\r\n`).join('')}content-length: ${body.length}\r\n\r\n`,
    'latin1'), head ? Buffer.alloc(0) : body]);
  const observeLines = (cell: ConsoleOriginCell, status: number, lines: readonly RawHeader[], body: Buffer) =>
    observeExchange(cell, exchangeOf(buildCellRequest(cell, runInput()), encodeLines(status, lines, body, cell.method === 'HEAD')));
  /** The cell's restored answer with `name` written once per value, after every other header line. */
  const repeated = (cell: ConsoleOriginCell, name: string, values: readonly string[]) => {
    const response = restoredResponse(cell);
    return observeLines(cell, response.status, [...Object.entries(response.headers).filter(([key]) => key !== name),
      ...values.map(value => [name, value] as const)], response.body);
  };
  const absentGet = first('absent'), absentHead = first('absent', 'HEAD'), documentGet = first('document');
  hold(verdict(swap(absentGet.id, repeated(absentGet, 'cache-control', [outcomes.notFound.cacheControl]))).passed
    && verdict(swap(documentGet.id, repeated(documentGet, 'content-security-policy', [csp]))).passed, 'L3.09-local-lines-restored');
  for (const [cell, name, values, check] of [
    [absentGet, 'cache-control', ['public', outcomes.notFound.cacheControl], 'L3.09-not-found-contract'], // canonical last
    [absentGet, 'cache-control', [outcomes.notFound.cacheControl, 'public'], 'L3.09-not-found-contract'], // canonical first
    [absentHead, 'content-type', ['text/html; charset=utf-8', outcomes.notFound.contentType], 'L3.09-not-found-contract'],
    [absentHead, 'content-type', [outcomes.notFound.contentType, 'text/html; charset=utf-8'], 'L3.09-not-found-contract'],
    // A CSP split across two lines is ONE joined value: ',' => a second enforced policy.
    [documentGet, 'content-security-policy', ['script-src https://evil.example', csp], 'L3.09-document-csp'],
    [documentGet, 'content-security-policy', [csp, `report-uri ${unrelated}`], 'L3.09-document-csp'],
    [documentGet, 'content-security-policy', [csp.split('; ').slice(0, 4).join('; '), csp.split('; ').slice(4).join('; ')], 'L3.09-document-csp'],
  ] as const) hold(red(swap(cell.id, repeated(cell, name, values)), check), 'L3.09-local-red-folded-header');
  // Green: empty directives, an extra upper-case non-canonical directive, and the module's full policy
  // (console-origin.mjs:45-57 order and directives) all hold.
  const modulePolicy = ["default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data: blob:", "font-src 'self'",
    "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'none'", `frame-ancestors ${publicOrigin}`,
    `report-uri ${reportPath}`].join('; ');
  for (const policy of [csp.replace('; ', '; ; '), `STYLE-SRC 'self'; ${csp}`, modulePolicy])
    hold(verdict(withPolicy(policy)).passed, 'L3.09-local-csp3-restored');
  mutate(first('document'), header('access-control-allow-origin', '*'), 'L3.09-document-headers');
  mutate(first('document'), header('referrer-policy'), 'L3.09-document-headers');
  mutate(first('asset'), header('cache-control', 'no-cache'), 'L3.09-asset-contract');
  mutate(first('asset', 'HEAD'), header('content-security-policy', "default-src 'self'"), 'L3.09-asset-contract');
  mutate(first('asset', 'HEAD'), header('content-type', 'text/plain'), 'L3.09-asset-contract');
  mutate(first('asset'), response => ({ ...response, body: Buffer.from('fixture-jz') }), 'L3.09-asset-bytes');
  mutate(first('api-root'), response => ({ ...response, status: 200 }), 'L3.09-api-root');
  mutate(first('api-root', 'HEAD'), header('content-type', 'text/html; charset=utf-8'), 'L3.09-api-root');
  mutate(first('no-leak'), response => ({ ...response, body: Buffer.from('root:x:0:0:root:/root:/bin/sh') }), 'L3.09-no-leak');
  const leak = first('no-leak');
  hold(red(swap(leak.id, { ...restored.find(row => row.cell === leak.id)!, transportError: true }), 'L3.09-transport'), 'L3.09-local-red-transport');
  hold(red([...restored, restored[0]], 'L3.09-duplicate-cell'), 'L3.09-local-red-duplicate');

  // (1) CSP report destination: compared with CSP_REPORT_PATH (console-origin.mjs:37), never
  // learned from the response. Both headers moved consistently on EVERY document => red.
  for (const destination of ['https://unrelated.example/collect', 'https://outside.invalid/report', '/fixture-csp-report']) {
    const moved = restored.map(row => {
      const cell = sent.find(entry => entry.id === row.cell)!;
      return cell.kind !== 'document' ? row : observe(cell, header('content-security-policy-report-only', reportOnlyWith(destination))(
        header('content-security-policy', cspWith(destination))(restoredResponse(cell))));
    });
    hold(red(moved, 'L3.09-document-csp'), 'L3.09-local-red-csp-report-destination');
  }

  // (3) Document body: every document route, /omega/console/console.html included.
  const documents = sent.filter(cell => cell.kind === 'document');
  const consoleHtml = documents.find(cell => cell.target === '/omega/console/console.html');
  hold(documents.length === 4 && !!consoleHtml, 'L3.09-local-document-routes');
  for (const cell of documents) {
    // Arbitrary bytes with a matching Content-Length (encodeResponse derives it from the body).
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), body: Buffer.from('<!doctype html><title>Console</title><p>other') })),
      'L3.09-document-body'), 'L3.09-local-red-document-arbitrary-body');
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), body: Buffer.alloc(0) })), 'L3.09-document-body'),
      'L3.09-local-red-document-empty-body');
  }

  // (5) 1xx informational responses ahead of the final one.
  const interimObserve = (cell: ConsoleOriginCell, response: FixtureResponse, prefix: Buffer, extra = Buffer.alloc(0)) =>
    observeExchange(cell, exchangeOf(buildCellRequest(cell, runInput()), Buffer.concat([prefix, encodeResponse(response, cell.method === 'HEAD'), extra])));
  const interimRestored = verdict(sent.map(cell => interimObserve(cell, restoredResponse(cell), Buffer.concat([proceed, hints]))));
  hold(interimRestored.passed && interimRestored.failures.length === 0, 'L3.09-local-interim-restored');
  for (const cell of sent.filter(row => row.kind === 'no-leak'))
    hold(red(swap(cell.id, interimObserve(cell, { status: 200, headers: { 'content-type': 'text/plain' },
      body: Buffer.from('outside the build\nroot:x:0:0:root:/root:/bin/sh') }, hints)), 'L3.09-no-leak'), 'L3.09-local-red-interim-leak');
  const interimHead = first('absent', 'HEAD');
  hold(red(swap(interimHead.id, interimObserve(interimHead, json404(), hints, Buffer.from('body'))), 'L3.09-head-body'),
    'L3.09-local-red-interim-head-body');
  for (const cell of [first('absent'), first('no-leak'), byCase('csp-report')])
    hold(red(swap(cell.id, interimObserve(cell, restoredResponse(cell), switching)), 'L3.09-transport'), 'L3.09-local-red-switching');

  // (2) Transport: the SAME driveRawExchange the live path uses, on a scripted socket.
  const viaTransport = (cell: ConsoleOriginCell, script: readonly RawScriptEvent[]) => {
    const run = simulateRawExchange(buildCellRequest(cell, runInput()), cell.method === 'HEAD', script);
    return run.exchange ? observeExchange(cell, run.exchange) : null;
  };
  const answered = (cell: ConsoleOriginCell): RawScriptEvent[] => [
    { at: 1, event: 'data', bytes: encodeResponse(restoredResponse(cell), cell.method === 'HEAD') },
    { at: 2, event: 'end' }, { at: 2, event: 'close' }];
  const transportRestored = sent.map(cell => viaTransport(cell, answered(cell)));
  hold(transportRestored.every(row => row !== null)
    && verdict(transportRestored as ConsoleOriginObservation[]).passed, 'L3.09-local-transport-restored');
  const headBlock = (cell: ConsoleOriginCell) => encodeResponse(restoredResponse(cell), true);
  for (const cell of heads) {
    // Counterexample: headers at 1 ms, forbidden body at 1001 ms, EOF at 1100 ms.
    const delayed = viaTransport(cell, [{ at: 1, event: 'data', bytes: headBlock(cell) }, { at: 1001, event: 'data', bytes: Buffer.from('body') },
      { at: 1100, event: 'end' }, { at: 1100, event: 'close' }]);
    hold(!!delayed && red(swap(cell.id, delayed), 'L3.09-head-body'), 'L3.09-local-red-head-delayed-body');
  }
  const headCell = first('absent', 'HEAD');
  const waited = simulateRawExchange(buildCellRequest(headCell, runInput()), true, [{ at: 1, event: 'data', bytes: headBlock(headCell) },
    { at: 1001, event: 'data', bytes: Buffer.from('body') }, { at: 1100, event: 'end' }]);
  hold(waited.finishedAt === 1100 && waited.exchange?.closed === true
    && waited.exchange.received.length === headBlock(headCell).length + 4, 'L3.09-local-head-waits-for-eof');
  // Silence is not proof: headers then an open socket (with or without a later body) until the timeout.
  const silent = viaTransport(headCell, [{ at: 1, event: 'data', bytes: headBlock(headCell) }]);
  hold(!!silent && red(swap(headCell.id, silent), 'L3.09-transport'), 'L3.09-local-red-head-open');
  const openBody = viaTransport(headCell, [{ at: 1, event: 'data', bytes: headBlock(headCell) }, { at: 1001, event: 'data', bytes: Buffer.from('x') }]);
  hold(!!openBody && red(swap(headCell.id, openBody), 'L3.09-transport'), 'L3.09-local-red-head-open-body');
  const timedOut = simulateRawExchange(buildCellRequest(headCell, runInput()), true, [{ at: 1, event: 'data', bytes: headBlock(headCell) }]);
  hold(timedOut.finishedAt === RAW_TIMEOUT_MS && timedOut.exchange?.transportError === true, 'L3.09-local-head-timeout');
  const reset = viaTransport(headCell, [{ at: 1, event: 'data', bytes: headBlock(headCell) }, { at: 2, event: 'close' }]);
  hold(!!reset && red(swap(headCell.id, reset), 'L3.09-transport'), 'L3.09-local-red-head-close-without-eof');
  // Belt and braces: a HEAD exchange handed over without EOF is a transport failure in observeExchange too.
  const unclosed = observeExchange(headCell, { ...exchangeOf(buildCellRequest(headCell, runInput()), headBlock(headCell)), closed: false });
  hold(red(swap(headCell.id, unclosed), 'L3.09-transport'), 'L3.09-local-red-head-unclosed');
  const getCell = first('absent');
  const errored = viaTransport(getCell, [{ at: 1, event: 'error' }]);
  hold(!!errored && red(swap(getCell.id, errored), 'L3.09-transport'), 'L3.09-local-red-transport-error');
  // GET still ends on its framing (no EOF needed), across chunks split in time.
  const full = encodeResponse(json404(), false);
  const split = viaTransport(getCell, [{ at: 1, event: 'data', bytes: full.subarray(0, 10) }, { at: 1001, event: 'data', bytes: full.subarray(10) }]);
  hold(!!split && verdict(swap(getCell.id, split)).passed, 'L3.09-local-get-framing');

  // (1) Coded content: every body predicate reads DECODED content; any coding is red.
  const chunkedResponse = (response: FixtureResponse, extra: Record<string, string> = {}) => {
    const block = Object.entries({ ...response.headers, 'transfer-encoding': 'chunked', ...extra })
      .map(([name, value]) => `${name}: ${value}\r\n`).join('');
    const chunks: Buffer[] = [];
    for (let at = 0; at < response.body.length; at += 7) {
      const piece = response.body.subarray(at, at + 7);
      chunks.push(Buffer.from(`${piece.length.toString(16)}\r\n`, 'latin1'), piece, Buffer.from('\r\n', 'latin1'));
    }
    return Buffer.concat([Buffer.from(`HTTP/1.1 ${response.status} Fixture\r\n${block}\r\n`, 'latin1'), ...chunks,
      Buffer.from('0\r\n\r\n', 'latin1')]);
  };
  const chunkedRestored = viaTransport(getCell, [{ at: 1, event: 'data', bytes: chunkedResponse(json404()) }]);
  hold(!!chunkedRestored && verdict(swap(getCell.id, chunkedRestored)).passed, 'L3.09-local-chunked-restored');
  const identityRestored = verdict(sent.map(cell => observe(cell, header('content-encoding', 'identity')(restoredResponse(cell)))));
  hold(identityRestored.passed && identityRestored.failures.length === 0, 'L3.09-local-identity-restored');
  const coded = (encoding: string, bytes: Buffer, status = 404): FixtureResponse => ({ status,
    headers: { 'content-type': 'text/plain', 'content-encoding': encoding }, body: bytes });
  const codedLeaks: [string, Buffer][] = [['gzip', gzipSync(plain)], ['x-gzip', gzipSync(plain)], ['br', brotliCompressSync(plain)],
    ['deflate', deflateSync(plain)], ['deflate', deflateRawSync(plain)], ['gzip, br', brotliCompressSync(gzipSync(plain))]];
  const both = (rows: readonly ConsoleOriginObservation[], ...checks: string[]) => {
    const result = verdict(rows);
    return !result.passed && checks.every(check => result.failures.includes(check));
  };
  const leakCells = sent.filter(cell => cell.kind === 'no-leak');
  hold(leakCells.length === 5, 'L3.09-local-no-leak-cells');
  for (const cell of leakCells) {
    for (const [encoding, bytes] of codedLeaks) for (const status of [200, 404])
      hold(both(swap(cell.id, observe(cell, coded(encoding, bytes, status))), 'L3.09-no-leak', 'L3.09-content-encoding'),
        'L3.09-local-red-coded-leak');
    // Counterexample through the SAME transport: gzip with Content-Length framing, then chunked framing.
    const gzipLeak = coded('gzip', gzipSync(plain), 200);
    for (const bytes of [encodeResponse(gzipLeak, false), chunkedResponse(gzipLeak)]) {
      const row = viaTransport(cell, [{ at: 1, event: 'data', bytes }]);
      hold(!!row && !row.transportError && both(swap(cell.id, row), 'L3.09-no-leak', 'L3.09-content-encoding'),
        'L3.09-local-red-gzip-leak-transport');
    }
    // Unknown, empty or undecodable coding: unreadable content is never cleared.
    for (const [encoding, bytes] of [['zstd', gzipSync(plain)], ['compress', Buffer.from('opaque')], ['', Buffer.from('opaque')],
      ['gzip', Buffer.from('not gzip')], ['br', Buffer.from('not br')], ['gzip, zstd', gzipSync(plain)],
      ['gzip\u00a0', gzipSync(plain)], ['\u00a0gzip', gzipSync(plain)], ['gzip,\u00a0br', brotliCompressSync(gzipSync(plain))]] as const)
      hold(both(swap(cell.id, observe(cell, coded(encoding, bytes))), 'L3.09-no-leak', 'L3.09-content-encoding'),
        'L3.09-local-red-unknown-coding');
    // A transfer coding beyond chunked: the dechunked bytes are still coded.
    const transferCoded = viaTransport(cell, [{ at: 1, event: 'data', bytes: chunkedResponse({ status: 200,
      headers: { 'content-type': 'text/plain' }, body: gzipSync(plain) }, { 'transfer-encoding': 'gzip, chunked' }) }]);
    hold(!!transferCoded && both(swap(cell.id, transferCoded), 'L3.09-no-leak', 'L3.09-content-encoding'), 'L3.09-local-red-transfer-coding');
  }
  hold(both(swap(leakCells[0].id, observe(leakCells[0], coded('gzip', bomb))), 'L3.09-no-leak', 'L3.09-content-encoding'),
    'L3.09-local-red-coded-bomb');
  // A coded but otherwise CORRECT answer is still red, and the predicates read the decoded bytes
  // (the decoded 404 JSON / document hold, so only the coding fails).
  const absentCell = first('absent'), documentCell = first('document');
  const codedOk = (cell: ConsoleOriginCell) => { const response = restoredResponse(cell);
    return { ...response, headers: { ...response.headers, 'content-encoding': 'gzip' }, body: gzipSync(response.body) }; };
  const coded404 = verdict(swap(absentCell.id, observe(absentCell, codedOk(absentCell))));
  hold(!coded404.passed && coded404.failures.includes('L3.09-content-encoding') && !coded404.failures.includes('L3.09-not-found-contract'),
    'L3.09-local-red-coded-correct-404');
  const codedDocument = verdict(swap(documentCell.id, observe(documentCell, codedOk(documentCell))));
  hold(!codedDocument.passed && codedDocument.failures.includes('L3.09-content-encoding')
    && !codedDocument.failures.includes('L3.09-document-body'), 'L3.09-local-red-coded-document');
  mutate(first('spa', 'HEAD'), header('content-encoding', 'gzip'), 'L3.09-content-encoding');
  mutate(first('asset'), header('content-encoding', 'br'), 'L3.09-content-encoding');
  // A runner that drops or changes the identity request is caught by the whole-request check.
  for (const replacement of ['', 'Accept-Encoding: gzip, deflate, br\r\n']) {
    const written = Buffer.from(buildCellRequest(leakCells[0], runInput()).toString('latin1')
      .replace('Accept-Encoding: identity\r\n', replacement), 'latin1');
    hold(red(swap(leakCells[0].id, { ...restored.find(row => row.cell === leakCells[0].id)!, written }), 'L3.09-raw-request'),
      'L3.09-local-red-accept-encoding');
  }

  // (4) Listener API and CSP report cases.
  const browserCell = byCase('browser-token'), foreignCell = byCase('foreign-origin'), reportCell = byCase('csp-report');
  const oversizedCell = byCase('csp-oversized');
  mutate(browserCell, response => ({ ...response, body: Buffer.from('{"error":"omega_assertion_rejected"}') }), 'L3.09-api-error');
  mutate(browserCell, header('x-content-type-options'), 'L3.09-api-headers');
  mutate(foreignCell, response => ({ ...response, body: Buffer.from('{"error":"route_not_allowed"}') }), 'L3.09-api-error');
  const reportBytes = buildCellRequest(reportCell, runInput()).toString('latin1');
  const oversizedBytes = buildCellRequest(oversizedCell, runInput());
  hold(reportBytes.includes('\r\nContent-Type: application/csp-report\r\n')
    && reportBytes.endsWith('"document-uri":"http://127.0.0.1:9/agents/secret-agent"}}')
    && oversizedBytes.toString('latin1').includes('\r\nContent-Type: text/plain;charset=UTF-8\r\nContent-Length: 20000\r\n')
    && oversizedBytes.subarray(oversizedBytes.indexOf(HEADER_END) + 4).equals(Buffer.alloc(20000, 'x')), 'L3.09-local-csp-report-bytes');
  const browserBytes = buildCellRequest(browserCell, runInput()).toString('latin1');
  const foreignBytes = buildCellRequest(foreignCell, runInput()).toString('latin1');
  hold(browserBytes.includes(`\r\nAuthorization: Bearer ${credentials.browserToken}\r\n`) && !browserBytes.includes('\r\nOrigin:')
    && foreignBytes.includes(`\r\nAuthorization: Bearer ${credentials.assertions['foreign-origin']}\r\nOrigin: https://evil.example\r\n`)
    && buildCellRequest(byCase('fa-origin'), runInput()).toString('latin1').includes(`\r\nOrigin: ${publicOrigin}\r\n`)
    && buildCellRequest(byCase('assertion'), runInput()).toString('latin1').includes('\r\nOrigin: http://127.0.0.1:9\r\n'),
  'L3.09-local-api-request-bytes');
  // A runner that drops/changes a planned header or body is caught by the whole-request check.
  const withWritten = (cell: ConsoleOriginCell, written: Buffer) => ({ ...restored.find(row => row.cell === cell.id)!, written });
  for (const [cell, written] of [
    [foreignCell, buildRawRequest('GET', foreignCell.target!, host, '*/*', [['Authorization', `Bearer ${credentials.assertions['foreign-origin']}`]])],
    [browserCell, buildRawRequest('GET', browserCell.target!, host, '*/*', [['Authorization', `Bearer ${credentials.assertions.assertion}`]])],
    [reportCell, buildRawRequest('POST', reportCell.target!, host, '*/*', [['Content-Type', 'application/csp-report']], Buffer.from('{}'))],
    [first('absent'), buildRawRequest('GET', first('absent').target!, host, first('absent').accept, [['X-Extra', '1']])],
  ] as const) hold(red(swap(cell.id, withWritten(cell, written)), 'L3.09-raw-request'), 'L3.09-local-red-raw-request');
  // Value-free: no credential value in any cell, verdict or receipt (passing or failing).
  const secrets = [credentials.browserToken, ...Object.values(credentials.assertions)];
  const leaks = (value: unknown) => secrets.some(secret => JSON.stringify(value).includes(secret));
  const failing = verdict(swap(browserCell.id, observe(browserCell, { ...restoredResponse(browserCell), status: 200 })));
  hold(!failing.passed && !leaks(failing) && !leaks(restoredVerdict) && !leaks(plan), 'L3.09-local-credential-value-free');
  const noCredentials = { url: binding.url, appListenerUrl, document: binding.document, assets: binding.assets };
  const credentialGaps = CREDENTIAL_SLOTS.map(slot => slot.gap);
  const credentialSent = new Set(planConsoleOriginMatrix(noCredentials).filter(cell => cell.target !== null).map(cell => cell.id));
  const withoutCredentials = restored.filter(row => credentialSent.has(row.cell));
  const credentialVerdict = verdict(withoutCredentials, noCredentials);
  hold(!credentialVerdict.passed && credentialVerdict.failures.length === 0 && credentialGaps.every(gap => credentialVerdict.blocked.includes(gap))
    && credentialVerdict.blocked.length === credentialGaps.length
    // 5 Console-listener api cells + the app-listener api cell (500 shares the browserToken slot).
    && credentialVerdict.cells.filter(cell => cell.verdict === 'blocked').length === 6
    && credentialVerdict.cells.find(cell => cell.cell.startsWith('app-api#'))?.verdict === 'blocked'
    && credentialVerdict.cells.filter(cell => cell.cell.startsWith('app-absent#')).every(cell => cell.verdict === 'pass')
    && credentialVerdict.cells.filter(cell => cell.cell.startsWith('csp-report#')).every(cell => cell.verdict === 'pass')
    && credentialVerdict.cells.every(cell => cell.verdict !== 'blocked' || Object.keys(cell).join() === 'cell,verdict'),
  'L3.09-local-credential-blocked');
  hold(red(restored, 'L3.09-unplanned-cell', noCredentials), 'L3.09-local-red-credential-unplanned');
  const reportBroken = verdict(withoutCredentials.map(row => row.cell === reportCell.id
    ? observe(reportCell, { status: 200, headers: {}, body: Buffer.from('ok') }, reportCell.target!, Buffer.alloc(0), noCredentials) : row), noCredentials);
  hold(!reportBroken.passed && reportBroken.failures.includes('L3.09-csp-report-status'), 'L3.09-local-credential-failed-beats-blocked');
  // The app-listener 404s still fail while its api cell is BLOCKED for want of the browser token.
  const appStaticCell = sent.find(cell => cell.kind === 'app-absent')!;
  const appBroken = verdict(withoutCredentials.map(row => row.cell === appStaticCell.id
    ? observe(appStaticCell, spaDocument(), appStaticCell.target!, Buffer.alloc(0), noCredentials) : row), noCredentials);
  hold(!appBroken.passed && appBroken.failures.includes('L3.09-app-absent-status'), 'L3.09-local-app-failed-beats-blocked');
  const [browserGap, assertionGap, foreignGap, faGap, operatorGap] = credentialGaps;
  for (const [bad, gaps] of [
    [{ ...credentials, browserToken: 'omega_delegate_x.y.z' }, [browserGap]],
    [{ ...credentials, browserToken: 'two words' }, [browserGap]],
    [{ ...credentials, browserToken: 'token\r\nX-Injected: 1' }, [browserGap]],
    [{ ...credentials, assertions: { ...credentials.assertions, assertion: 'fixture-browser-like' } }, [assertionGap]],
    [{ ...credentials, assertions: { ...credentials.assertions, operator: 'omega_delegate_two.segments' } }, [operatorGap]],
    [{ ...credentials, assertions: { ...credentials.assertions, 'fa-origin': credentials.assertions['foreign-origin'] } }, [foreignGap, faGap]],
    ['not-a-record', credentialGaps],
  ] as const) {
    const result = verdict([], { ...binding, credentials: bad });
    hold(result.blocked.length === gaps.length && gaps.every(gap => result.blocked.includes(gap)), 'L3.09-local-credential-malformed-blocked');
  }

  // (3) App listener: 499-503 and 506-522, sent to binding.appListenerUrl.
  const appCell = byCase('sandboxes');
  mutate(appCell, response => ({ ...response, status: 401, body: Buffer.from('{"error":"omega_assertion_required"}') }), 'L3.09-app-api-status');
  mutate(appCell, response => ({ ...response, status: 403, body: Buffer.from('{"error":"origin_not_allowed"}') }), 'L3.09-app-api-status');
  mutate(appCell, header('access-control-allow-origin'), 'L3.09-app-api-headers');
  mutate(appCell, header('access-control-allow-origin', '*'), 'L3.09-app-api-headers');
  mutate(appCell, header('access-control-allow-origin', binding.url), 'L3.09-app-api-headers');
  mutate(appCell, header('access-control-allow-origin', `${publicOrigin}, https://evil.example`), 'L3.09-app-api-headers');
  mutate(appCell, header('access-control-allow-origin', `${publicOrigin}/`), 'L3.09-app-api-headers');
  const appBytes = buildCellRequest(appCell, runInput()).toString('latin1');
  hold(appBytes === `GET /omega/api/workspace/sandboxes HTTP/1.1\r\nHost: ${appHost}\r\nAccept: */*\r\nAccept-Encoding: identity\r\n`
    + `Authorization: Bearer ${credentials.browserToken}\r\nOrigin: ${publicOrigin}\r\nConnection: close\r\n\r\n`, 'L3.09-local-app-request-bytes');
  const appStatic = first('app-absent', 'HEAD');
  hold(buildCellRequest(appStatic, runInput()).equals(buildRawRequest('HEAD', '/', appHost, 'text/html')), 'L3.09-local-app-static-bytes');
  // Wrong listener, dropped/wrong Origin, or an assertion instead of the browser token => raw-request red.
  const browserAuth: RawHeader = ['Authorization', `Bearer ${credentials.browserToken}`];
  for (const [cell, written] of [
    [appCell, buildRawRequest('GET', appCell.target!, appHost, '*/*', [browserAuth])],
    [appCell, buildRawRequest('GET', appCell.target!, host, '*/*', [browserAuth, ['Origin', publicOrigin]])],
    [appCell, buildRawRequest('GET', appCell.target!, appHost, '*/*', [browserAuth, ['Origin', binding.url]])],
    [appCell, buildRawRequest('GET', appCell.target!, appHost, '*/*', [['Authorization', `Bearer ${credentials.assertions.assertion}`],
      ['Origin', publicOrigin]])],
    [appStatic, buildRawRequest('HEAD', '/', host, 'text/html')],
  ] as const) hold(red(swap(cell.id, withWritten(cell, written)), 'L3.09-raw-request'), 'L3.09-local-red-app-request');
  const appFailing = verdict(swap(appCell.id, observe(appCell, { ...restoredResponse(appCell), status: 401 })));
  hold(!appFailing.passed && !leaks(appFailing), 'L3.09-local-app-value-free');
  // No app listener bound: all 15 app cells BLOCKED value-free, nothing sent, nothing else affected.
  const noApp = { url: binding.url, document: binding.document, assets: binding.assets, credentials };
  const appSent = new Set(planConsoleOriginMatrix(noApp).filter(cell => cell.target !== null).map(cell => cell.id));
  const appVerdict = verdict(restored.filter(row => appSent.has(row.cell)), noApp);
  hold(!appVerdict.passed && appVerdict.failures.length === 0 && appVerdict.blocked.join() === APP_LISTENER_GAP
    && appVerdict.cells.filter(cell => cell.verdict === 'blocked').length === 15
    && appVerdict.cells.filter(cell => cell.cell.startsWith('app-')).every(cell => cell.verdict === 'blocked')
    && appVerdict.cells.every(cell => cell.verdict !== 'blocked' || Object.keys(cell).join() === 'cell,verdict'), 'L3.09-local-app-listener-blocked');
  hold(red(restored, 'L3.09-unplanned-cell', noApp), 'L3.09-local-red-app-unplanned');
  for (const [value, gap] of [[undefined, APP_LISTENER_GAP], [7, APP_LISTENER_GAP], ['', APP_LISTENER_GAP],
    ['http://127.0.0.1:7/', APP_LISTENER_GAP], ['http://127.0.0.1:7/omega/api', APP_LISTENER_GAP], ['http://u:p@127.0.0.1:7', APP_LISTENER_GAP],
    ['ftp://127.0.0.1:7', APP_LISTENER_GAP], [binding.url, APP_LISTENER_GAP], ['https://127.0.0.1:7', APP_LISTENER_TRANSPORT_GAP]] as const) {
    const bad = { ...binding, appListenerUrl: value };
    const result = verdict([], bad);
    hold(result.blocked.join() === gap && planConsoleOriginMatrix(bad).filter(cell => APP_KINDS.includes(cell.kind))
      .every(cell => cell.target === null && cell.gap === gap), 'L3.09-local-app-listener-malformed-blocked');
  }

  // Binding gaps: BLOCKED value-free, never guessed, and never masking a failure.
  const noAssets = { url: binding.url, appListenerUrl, document: binding.document, credentials };
  const executed = new Set(planConsoleOriginMatrix(noAssets).filter(cell => cell.target !== null).map(cell => cell.id));
  const withoutAssets = restored.filter(row => executed.has(row.cell));
  const gapVerdict = verdict(withoutAssets, noAssets);
  // Only immutable-js can BLOCK (ruling a): its 4 cells, while the 4 not-applicable cells stay not_applicable.
  hold(!gapVerdict.passed && gapVerdict.failures.length === 0 && gapVerdict.blocked.join() === 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS'
    && gapVerdict.cells.filter(cell => cell.verdict === 'blocked').length === 4
    && gapVerdict.cells.filter(cell => cell.verdict === 'not_applicable').length === 4
    && gapVerdict.cells.every(cell => cell.verdict !== 'blocked' || Object.keys(cell).join() === 'cell,verdict'), 'L3.09-local-asset-blocked');
  hold(red(restored, 'L3.09-unplanned-cell', noAssets), 'L3.09-local-red-unplanned');
  const anyAbsent = sent.find(cell => cell.kind === 'absent')!;
  const masked = verdict(withoutAssets.map(row => row.cell === anyAbsent.id ? observe(anyAbsent, spaDocument()) : row), noAssets);
  hold(!masked.passed && masked.failures.includes('L3.09-not-found-status'), 'L3.09-local-failed-beats-blocked');
  for (const [slot, bad, gap] of [
    ['immutable-js', { name: 'x/../app.js', ...fileOf(assetBytes['immutable-js']) }, 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS'],
    ['immutable-js', { name: jsName, byteLength: 1, sha256: 'not-a-digest' }, 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS'],
  ] as const) {
    const malformed = verdict([], { ...binding, assets: { ...binding.assets, [slot]: bad } });
    hold(malformed.blocked.length === 1 && malformed.blocked[0] === gap, 'L3.09-local-asset-malformed-blocked');
  }
  const noDocument = { url: binding.url, appListenerUrl, assets: binding.assets, credentials };
  const unboundDocument = verdict(restored, noDocument);
  hold(!unboundDocument.passed && unboundDocument.failures.length === 0 && unboundDocument.blocked.includes('J00_CONSOLE_ORIGIN_DOCUMENT')
    && unboundDocument.cells.filter(cell => cell.cell.startsWith('document#')).every(cell => cell.verdict === 'blocked'),
  'L3.09-local-document-blocked');
  const secondSpa = sent.filter(cell => cell.kind === 'spa' && cell.method === 'GET')[1];
  hold(red(swap(secondSpa.id, observe(secondSpa, { ...spaDocument(), body: Buffer.from('<!doctype html><title>Other</title>') })),
    'L3.09-spa-document', noDocument), 'L3.09-local-red-unbound-document-drift');
  // Unbound document: a console.html that differs from the common body is FAILED, not BLOCKED.
  if (consoleHtml) hold(red(swap(consoleHtml.id, observe(consoleHtml, { ...restoredResponse(consoleHtml), body: Buffer.from('other') })),
    'L3.09-document-body', noDocument), 'L3.09-local-red-unbound-document-body');

  // Empty files: an empty deployed document or asset never qualifies the wall.
  // Repro: binding.document = {0, sha256(empty)} and EVERY SPA/document answer empty (HEAD
  // Content-Length 0), everything else restored. It must be FAILED by the body/contract predicates (the
  // census throws BLOCKED only when there is no failure) AND the binding refused under its own seam.
  const emptyFile = fileOf(Buffer.alloc(0));
  hold(!sameFile(Buffer.alloc(0), emptyFile) && !sameFile(Buffer.alloc(0), { byteLength: 0, sha256: emptyFile.sha256 })
    && sameFile(documentBytes, fileOf(documentBytes)), 'L3.09-local-same-file-non-empty');
  const emptyDocument = { ...binding, document: emptyFile };
  const documentKinds: readonly Kind[] = ['spa', 'spa-dotted', 'document'];
  const emptied = restored.map(row => {
    const cell = sent.find(entry => entry.id === row.cell)!;
    return documentKinds.includes(cell.kind) ? observe(cell, { ...restoredResponse(cell), body: Buffer.alloc(0) }) : row;
  });
  for (const [bound, gap] of [[emptyDocument, 'J00_CONSOLE_ORIGIN_DOCUMENT_EMPTY'], [noDocument, 'J00_CONSOLE_ORIGIN_DOCUMENT']] as const) {
    const result = verdict(emptied, bound);
    hold(!result.passed && ['L3.09-spa-document', 'L3.09-document-body', 'L3.09-spa-contract'].every(check => result.failures.includes(check))
      && result.blocked.includes(gap), 'L3.09-local-red-empty-document');
  }
  // Either empty marker alone refuses the binding: a zero length, or the digest of zero bytes.
  for (const document of [emptyFile, { byteLength: documentBytes.length, sha256: emptyFile.sha256 },
    { byteLength: 0, sha256: fileOf(documentBytes).sha256 }]) {
    const result = verdict(restored, { ...binding, document });
    hold(!result.passed && result.blocked.includes('J00_CONSOLE_ORIGIN_DOCUMENT_EMPTY') && !result.blocked.includes('J00_CONSOLE_ORIGIN_DOCUMENT'),
      'L3.09-local-empty-document-blocked');
  }
  // Bound non-empty document: an empty answer on any SPA route (GET) is FAILED too.
  for (const cell of sent.filter(row => (row.kind === 'spa' || row.kind === 'spa-dotted') && row.method === 'GET'))
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), body: Buffer.alloc(0) })), 'L3.09-spa-document'),
      'L3.09-local-red-spa-empty-body');
  // Assets: a zero-byte immutable-js binding is refused (value-free, nothing sent); empty answers to its
  // cells are then unplanned, and an empty answer against a bound non-empty asset is FAILED.
  for (const file of [emptyFile, { byteLength: assetBytes['immutable-js'].length, sha256: emptyFile.sha256 }]) {
    const bound = { ...binding, assets: { ...binding.assets, 'immutable-js': { name: jsName, ...file } } };
    const result = verdict(restored.map(row => {
      const cell = sent.find(entry => entry.id === row.cell)!;
      return cell.kind === 'asset' ? observe(cell, { ...restoredResponse(cell), body: Buffer.alloc(0) }) : row;
    }), bound);
    hold(!result.passed && result.blocked.includes('J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS_EMPTY') && result.failures.includes('L3.09-unplanned-cell')
      && planConsoleOriginMatrix(bound).filter(cell => cell.kind === 'asset')
        .every(cell => cell.target === null && cell.gap === 'J00_CONSOLE_ORIGIN_ASSET_IMMUTABLE_JS_EMPTY'), 'L3.09-local-red-empty-asset');
  }
  for (const cell of sent.filter(row => row.kind === 'asset' && row.method === 'GET'))
    hold(red(swap(cell.id, observe(cell, { ...restoredResponse(cell), body: Buffer.alloc(0) })), 'L3.09-asset-bytes'),
      'L3.09-local-red-asset-empty-body');

  // ---- RULINGS (10-31): fixed in code ----
  const ruledIds = ['healthz', 'readyz', 'options-root', 'options-console', 'upper-case-path', 'upper-case-console', 'path-parameter',
    'encoded-trailing-slash', 'upper-encoded-dot-dot', 'backslash-separator', 'dotfile', 'assets-directory'];
  // By ruling id, so a ruled spelling planned ONCE as its canonical cell (mergedRuling) is found too.
  const ruledCells = (id: string) => sent.filter(cell => cell.ruledId === id);
  const receiptOf = (result: ConsoleOriginVerdict, cell: ConsoleOriginCell) => result.cells.find(row => row.cell === cell.id);
  const withRows = (cells: readonly ConsoleOriginCell[], response: (cell: ConsoleOriginCell) => FixtureResponse) => restored.map(row => {
    const cell = cells.find(entry => entry.id === row.cell);
    return cell ? observe(cell, response(cell)) : row;
  });
  // A binding cannot re-rule a cell: any `rulings` value leaves the verdict unchanged.
  for (const rulings of [Object.fromEntries(ruledIds.map(id => [id, 'allow'])), Object.fromEntries(ruledIds.map(id => [id, '404'])),
    { healthz: 'maybe' }, ['404']])
    hold(JSON.stringify(verdict(restored, { ...binding, rulings })) === JSON.stringify(restoredVerdict), 'L3.09-local-rulings-fixed');

  // Ruling a: the utf8-space-name / percent-space-name asset cells are NOT APPLICABLE to deployed builds.
  const naCells = plan.filter(cell => cell.kind === 'asset-encoded');
  hold(naCells.length === 4 && naCells.every(cell => cell.target === null && cell.gap === null) && sent.length === plan.length - 4
    && restoredVerdict.cells.filter(cell => cell.cell.startsWith('asset-encoded#'))
      .every(cell => cell.verdict === 'not_applicable' && Object.keys(cell).join() === 'cell,verdict'), 'L3.09-local-asset-not-applicable');
  // An answer for a not-applicable cell is unplanned: the runner must never send one.
  if (naCells[0]) hold(red([...restored.filter(row => row.cell !== naCells[0].id), observe(naCells[0],
    { status: 200, headers: { 'cache-control': 'no-cache' }, body: Buffer.from('encoded asset') }, ASSET_PREFIX + encodeURIComponent(utf8Name))],
  'L3.09-unplanned-cell'), 'L3.09-local-red-not-applicable-sent');
  // They never BLOCK: N/A slots unbound, malformed or empty change nothing.
  for (const assets of [{ 'immutable-js': binding.assets!['immutable-js'] }, { ...binding.assets,
    'utf8-space-name': { name: 'ascii.txt', ...fileOf(Buffer.from('a')) }, 'percent-space-name': { name: percentName, ...emptyFile } },
  { ...binding.assets, 'utf8-space-name': { name: utf8Name, ...fileOf(assetBytes['utf8-space-name']) },
    'percent-space-name': { name: percentName, ...fileOf(assetBytes['percent-space-name']) } }]) {
    const result = verdict(restored.filter(row => !row.cell.startsWith('asset-encoded#')), { ...binding, assets });
    hold(result.passed && result.blocked.length === 0, 'L3.09-local-asset-not-applicable-ignored');
  }

  // Ruling b: healthz/readyz ALLOW, status recorded, AND the GET body carries only a status.
  const healthCells = [...ruledCells('healthz'), ...ruledCells('readyz')];
  const healthGets = healthCells.filter(cell => cell.method === 'GET');
  const jsonBody = (status: number, body: string | Buffer): FixtureResponse => ({ status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }, body: Buffer.from(body) });
  hold(healthCells.length === 8 && healthGets.length === 4, 'L3.09-local-health-cells');
  // The gateway's 200 probe bodies (server.mjs:255, lifecycle.mjs:60 @46e39a4686, unchanged since 24a61e0424), the lifecycle objects as such
  // (lifecycle.mjs:52, 60: at 503 the gateway's own answer also carries requestId, see below) and the ruling's examples.
  for (const status of [200, 503]) for (const text of ['{"live":true}', '{"ready":true,"checks":{"ee":"ok","exchange":"ok"}}',
    '{"ready":false,"checks":{"draining":true}}', '{"ready":false,"checks":{"ee":"fail","exchange":"ok"}}', '{"status":"ok"}',
    '{"ok":true}', '{"status":"ok","ok":true}', 'ok', 'OK\n', '', '{}']) {
    const result = verdict(withRows(healthCells, () => jsonBody(status, text)));
    hold(result.passed && healthCells.every(cell => {
      const receipt = receiptOf(result, cell);
      return receipt?.verdict === 'recorded' && receipt.status === status && Object.keys(receipt).join() === 'cell,verdict,status';
    }), 'L3.09-local-health-recorded');
  }
  for (const status of [204, 404, 500])
    hold(verdict(withRows(healthCells, () => jsonBody(status, '{"live":true}'))).cells.filter(row => healthCells.some(cell => cell.id === row.cell))
      .every(row => row.verdict === 'recorded' && row.status === status), 'L3.09-local-health-status-recorded');
  // Only recorded: GET and HEAD may answer differently (no agreement rule for ruling b).
  const healthSplit = verdict(withRows(healthCells, cell => jsonBody(cell.method === 'HEAD' ? 405 : 200, '{"live":true}')));
  hold(healthSplit.passed && healthCells.every(cell => receiptOf(healthSplit, cell)?.status === (cell.method === 'HEAD' ? 405 : 200)),
    'L3.09-local-health-get-head-recorded');
  // Over STATUS_BODY_LIMIT although every member is allowed (a checks object of component names; the same form, shorter, holds).
  const statusEntries = Array.from({ length: 50 }, (_, at) => `"ok${String.fromCharCode(97 + Math.floor(at / 26), 97 + (at % 26))}":"ok"`);
  const longStatus = `{"checks":{${statusEntries.join(',')}}}`;
  hold(longStatus.length > STATUS_BODY_LIMIT && verdict(withRows(healthCells, () => jsonBody(200, `{"checks":{${statusEntries.slice(0, 5).join(',')}}}`))).passed
    && healthGets.every(cell => red(swap(cell.id, observe(cell, jsonBody(200, longStatus))), 'L3.09-ruled-status-body')), 'L3.09-local-health-long-form');
  for (const body of ['{"status":"ok","version":"1.4.2"}', '{"status":"ok","version":"beta"}', '{"ok":true,"build":"abc1234"}',
    '{"status":"ok","commit":"deadbeefcafe"}', '{"status":"deadbeef"}', 'deadbeefcafe', '1.4.2', 'v1', '{"status":"ok","host":"gateway"}',
    '{"status":"ok","hostname":"omega"}', '{"status":"omega-gw-7d9f8c6b5-x2x9z"}', '{"status":"gw.internal.example"}', 'localhost',
    '{"status":"ok","ip":"10.0.0.7"}', '{"status":"10.0.0.7"}', '{"status":"fe80::1"}', '{"status":"ok","uptime":12}', '{"status":200}',
    '{"status":"ok","checks":["ee"]}', '["ok"]', '{"status":null}', '{"status":"ok"', '{"status":"ok"} x', 'ok ready',
    `{"status":"${'a'.repeat(600)}"}`, longStatus, '{"a":{"b":{"c":{"d":true}}}}', Buffer.from([0x6f, 0xff])] as const)
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(200, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-body');
  // A member repeated at ANY depth. JSON.parse keeps only the last copy, so an
  // earlier copy could carry an IP address, a version, a build SHA or a hostname past the parsed-value checks.
  for (const body of ['{"status":"10.0.0.7","status":"ok"}', '{"status":"1.4.2","status":"ok"}', '{"status":"deadbeefcafe1234","status":"ok"}',
    '{"ok":"gw.internal.example","ok":true}', '{"checks":{"ee":"1.4.2","ee":"ok"}}', '{"status":"deadbeef","status":"ok"}',
    '{"checks":{"version":"1.4.2","hostname":"gw.internal.example","ip":"10.0.0.7"},"checks":{"ee":"ok"}}',
    '{"ready":true,"checks":{"ee":"ok","exchange":"ok","ee":"ok"}}', '{"status":"ok","status":"ok"}', '{"live":true,"live":true}'])
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(200, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-duplicate');
  // Keys outside the allowlist {status, ok, live, ready, checks, requestId} (alphabetic metadata a
  // denylist missed), a known key with the wrong type, and a check component that names an identity field.
  for (const body of ['{"status":"ok","fqdn":"gatewayeast"}', '{"status":"ok","node_name":"omegagw"}', '{"status":"ok","pod_name":"gatewayeast"}',
    '{"status":"ok","served_by":"gatewayprod"}', '{"status":"ok","machine":"x"}', '{"status":"ok","fqdn":"omega"}', '{"status":"ok","token":"secret"}',
    '{"Status":"ok"}', '{"ready":true,"checks":{"ee":"ok"},"extra":true}', '{"live":"yes"}', '{"ready":"true"}', '{"ok":"ok"}', '{"status":true}',
    '{"checks":"ok"}', '{"checks":true}', '{"checks":{"ee":{"state":"ok"}}}', '{"checks":{"hostname":"ok"}}', '{"checks":{"fqdn":"ok"}}',
    '{"checks":{"node_name":"ok"}}', '{"checks":{"ee":"ok","served_by":"ok"}}', '{"checks":{"token":"ok"}}', '{"status":"ok ready"}',
    '{"status":"gateway east"}', '{"status":""}', `{"status":"${'a'.repeat(33)}"}`, `{"checks":{"${'a'.repeat(33)}":"ok"}}`, '{"checks":{"":"ok"}}'])
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(200, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-key');
  // No escape can spell a key or value the raw scan would refuse (every allowed token is plain ASCII).
  for (const body of ['{"st\\u0061tus":"ok"}', '{"status":"\\u006fk"}', '{"status":"o\\nk"}', '{"status":"\\u0031\\u002e\\u0034"}',
    '{"checks":{"ee":"\\u006fk"}}', '{"status":"ok "}'])
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(200, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-escape');
  // Bodies the allowlist alone admits and only the raw-byte scan refuses ('localhost' inside a status word), also beside a
  // requestId (its value is the ONLY span the scan skips); and prototype member names, which a plain-object lookup would admit.
  for (const [status, body] of [[200, '{"status":"isLocalhost"}'], [200, 'servedFromLOCALHOST'], [200, '{"checks":{"ee":"localhostok"}}'],
    [503, '{"status":"isLocalhost","requestId":"0123456789abcdef0123456789abcdef"}'], [200, '{"constructor":true}'], [200, '{"__proto__":true}'],
    [200, '{"toString":"ok"}'], [200, '{"hasOwnProperty":true}']] as const)
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(status, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-raw-scan');
  // A not-ready or draining readyz exactly as the gateway answers it: respond() appends requestId, the 32-hex
  // per-request trace id, to every body with status >= 400 (server.mjs:201, trace.mjs:38); lifecycle.mjs:52 and 60
  // give the rest. Interpretation of ruling b: a trace id is not build information, so it is allowed there, once.
  const requestIds = ['0123456789abcdef0123456789abcdef', 'abcdefabcdefabcdefabcdefabcdefab'];
  for (const requestId of requestIds) for (const status of [503, 500, 404])
    for (const text of [`{"ready":false,"checks":{"ee":"fail","exchange":"ok"},"requestId":"${requestId}"}`,
      `{"ready":false,"checks":{"draining":true},"requestId":"${requestId}"}`, `{"requestId":"${requestId}","ready":false}`]) {
      const result = verdict(withRows(healthCells, () => jsonBody(status, text)));
      hold(result.passed && healthCells.every(cell => receiptOf(result, cell)?.verdict === 'recorded' && receiptOf(result, cell)?.status === status),
        'L3.09-local-health-request-id');
    }
  // ... and nowhere else: not on a 2xx body, never malformed, never twice, never inside another member or value.
  const [hexId] = requestIds;
  for (const [status, body] of [[200, `{"ready":true,"requestId":"${hexId}"}`], [201, `{"live":true,"requestId":"${hexId}"}`],
    [399, `{"requestId":"${hexId}"}`], [503, `{"ready":false,"requestId":"${hexId.toUpperCase()}"}`],
    [503, `{"ready":false,"requestId":"${hexId.slice(1)}"}`], [503, `{"ready":false,"requestId":"${hexId}0"}`],
    [503, `{"ready":false,"requestId":"${hexId.slice(1)}g"}`], [503, `{"ready":false,"requestId":"${hexId}","requestId":"${hexId}"}`],
    [503, '{"ready":false,"requestId":1234567}'], [503, '{"ready":false,"requestId":true}'],
    [503, `{"ready":false,"checks":{"requestId":"${hexId}"}}`], [503, `{"status":"${requestIds[1]}"}`],
    [503, `{"ready":false,"requestId":"${hexId}","version":"beta"}`], [503, `{"ready":false,"requestId":"10.0.0.7.10.0.0.7.10.0.0.7.10.0."}`],
    [503, `${hexId}`]] as const)
    for (const cell of healthGets)
      hold(red(swap(cell.id, observe(cell, jsonBody(status, body))), 'L3.09-ruled-status-body'), 'L3.09-local-red-health-request-id');
  for (const cell of healthGets) {
    // A coded body is a contract failure, and an unreadable one cannot be cleared.
    const zipped = { ...jsonBody(200, ''), headers: { 'content-encoding': 'gzip' }, body: gzipSync(Buffer.from('{"live":true}')) };
    hold(red(swap(cell.id, observe(cell, zipped)), 'L3.09-content-encoding'), 'L3.09-local-red-health-coded');
    hold(red(swap(cell.id, observe(cell, { ...zipped, headers: { 'content-encoding': 'zstd' } })), 'L3.09-ruled-status-body'),
      'L3.09-local-red-health-unreadable');
    hold(red(swap(cell.id, { ...observe(cell, jsonBody(200, '{"live":true}')), transportError: true }), 'L3.09-transport'),
      'L3.09-local-red-health-transport');
  }
  for (const cell of healthCells.filter(row => row.method === 'HEAD'))
    hold(red(swap(cell.id, observe(cell, jsonBody(200, ''), cell.target!, Buffer.from('{"live":true}'))), 'L3.09-head-body'),
      'L3.09-local-red-health-head-body');

  // Ruling c: OPTIONS ALLOW, status recorded, AND no credentialed wildcard or reflected arbitrary Origin.
  // J00 sends the source's own arbitrary foreign origin (461/475) and a preflight method, so reflection is observable.
  const preflightCells = [...ruledCells('options-root'), ...ruledCells('options-console')];
  const arbitraryOrigin = 'https://evil.example';
  hold(preflightCells.length === 2 && LISTENER_CANON.apiCases.find(apiCase => apiCase.id === 'foreign-origin')?.origin === arbitraryOrigin
    && preflightCells.every(cell => cell.method === 'OPTIONS' && buildCellRequest(cell, runInput()).equals(buildRawRequest('OPTIONS',
      cell.target!, host, '*/*', [['Origin', arbitraryOrigin], ['Access-Control-Request-Method', 'GET']]))), 'L3.09-local-preflight-request-bytes');
  const preflight = (status: number, extra: Record<string, string>): FixtureResponse => ({ status,
    headers: { 'x-content-type-options': 'nosniff', ...extra }, body: Buffer.alloc(0) });
  for (const cell of preflightCells) {
    for (const extra of [{ 'access-control-allow-origin': '*', 'access-control-allow-credentials': 'true' },
      { 'access-control-allow-origin': arbitraryOrigin, 'access-control-allow-credentials': 'true' },
      { 'access-control-allow-origin': arbitraryOrigin.toUpperCase(), 'access-control-allow-credentials': 'TRUE' },
      { 'access-control-allow-origin': `${publicOrigin}, *`, 'access-control-allow-credentials': 'true' },
      { 'access-control-allow-origin': `${arbitraryOrigin}, ${publicOrigin}`, 'access-control-allow-credentials': 'true' }])
      for (const status of [204, 200])
        hold(red(swap(cell.id, observe(cell, preflight(status, extra))), 'L3.09-ruled-preflight-cors'), 'L3.09-local-red-preflight-cors');
    // A credentialed 'null' origin (what a sandboxed iframe or a file: page sends) is as open as a reflection.
    for (const extra of [{ 'access-control-allow-origin': 'null', 'access-control-allow-credentials': 'true' },
      { 'access-control-allow-origin': 'NULL', 'access-control-allow-credentials': 'True' },
      { 'access-control-allow-origin': `${publicOrigin}, null`, 'access-control-allow-credentials': 'true' }])
      for (const status of [204, 200])
        hold(red(swap(cell.id, observe(cell, preflight(status, extra))), 'L3.09-ruled-preflight-cors'), 'L3.09-local-red-preflight-null');
    // Without credentials, or credentials for a non-arbitrary (allow-listed) origin: recorded, not failed.
    const allowed: Record<string, string>[] = [{}, { 'access-control-allow-origin': '*' }, { 'access-control-allow-origin': arbitraryOrigin },
      { 'access-control-allow-origin': '*', 'access-control-allow-credentials': 'false' }, { 'access-control-allow-origin': 'null' },
      { 'access-control-allow-origin': publicOrigin, 'access-control-allow-credentials': 'true' }];
    for (const extra of allowed)
      for (const status of [204, 200, 404, 405]) {
        const result = verdict(swap(cell.id, observe(cell, preflight(status, extra))));
        const receipt = receiptOf(result, cell);
        hold(result.passed && receipt?.verdict === 'recorded' && receipt.status === status, 'L3.09-local-preflight-recorded');
      }
    // Folded duplicate lines: a credentialed reflection behind an allowed origin on an earlier line, either order.
    for (const origins of [[publicOrigin, arbitraryOrigin], [arbitraryOrigin, publicOrigin]])
      hold(red(swap(cell.id, observeLines(cell, 204, [...origins.map(origin => ['access-control-allow-origin', origin] as const),
        ['access-control-allow-credentials', 'true']], Buffer.alloc(0))), 'L3.09-ruled-preflight-cors'), 'L3.09-local-red-preflight-folded');
    // A runner that drops the Origin (or the preflight method) is caught by the whole-request check.
    for (const headers of [[], [['Origin', arbitraryOrigin]], [['Access-Control-Request-Method', 'GET']]] as const)
      hold(red(swap(cell.id, withWritten(cell, buildRawRequest('OPTIONS', cell.target!, host, '*/*', headers))), 'L3.09-raw-request'),
        'L3.09-local-red-preflight-request');
  }

  // Ruling d: exact 404 (the not-found shape plus the gateway's exact body), GET and HEAD agree, HEAD no body; never a
  // file body or a directory listing. The SPA-fallback mutant is what the gateway at 24a61e0424 answered for dotfile
  // and assets-directory (fixed at 46e39a4686: console-origin.mjs:141, 148-152, 177). Those two are planned ONCE, as
  // their canonical files-only cells (8 of the 24), which carry ruling d AND the canonical full-body rule.
  const exactCells = ['upper-encoded-dot-dot', 'backslash-separator', 'dotfile', 'assets-directory', 'encoded-trailing-slash',
    'path-parameter'].flatMap(ruledCells);
  const ruledOnlyExact = exactCells.filter(cell => cell.kind === 'ruled');
  hold(exactCells.length === 24 && ruledOnlyExact.length === 16 && exactCells.filter(cell => cell.kind === 'files-only').length === 8
    && exactCells.every(cell => receiptOf(restoredVerdict, cell)?.verdict === 'pass'), 'L3.09-local-ruled-404-restored');
  // A JSON 404 that also carries a listing or file bytes, a repeated or escaped member, or
  // anything beyond {"error":"not_found"} plus the gateway's requestId (server.mjs:201, 266) is not the exact 404.
  const jsonNot404 = ['{"error":"not_found","entries":["app-Ab12Cd34.js",".env"]}', '{"error":"not_found","directory":["secret.env","app.js"]}',
    '{"error":"not_found","file":"root:x:0:0:root:/root:/bin/sh"}', '{"error":"not_found","error":"not_found"}',
    '{"error":"root:x:0:0:root:/root:/bin/sh","error":"not_found"}', `{"error":"not_found","requestId":"${notFoundId}","path":"/omega/console/.env"}`,
    '{"error":"not_found","reason":"public_share_closed"}', '{"error":"not_found","listing":{}}', '{"error":"not_found","ok":true}',
    `{"error":"not_found","requestId":"${notFoundId.toUpperCase()}"}`, `{"error":"not_found","requestId":"${notFoundId.slice(1)}"}`,
    `{"error":"not_found","requestId":"${notFoundId}","requestId":"${notFoundId}"}`, '{"error":"not_found","requestId":12345}',
    `{"requestId":"${notFoundId}"}`, '{"error":"not\\u005ffound"}', '{"error":"not_found"}{"error":"not_found"}'];
  // The gateway's exact not-found bytes (requestId appended by respond(), server.mjs:201) and the same members reordered or spaced.
  // Ruling d leaves the requestId optional: the body without it holds on the 16 ruled-only cells, NOT on the 8 merged cells
  // (the canonical half requires it; L3.09-local-merged-404-stricter).
  for (const text of [`{"error":"not_found","requestId":"${notFoundId}"}`, `{"requestId":"${notFoundId}","error":"not_found"}`,
    '{ "error" : "not_found" }\n']) {
    const cells = text.includes('requestId') ? exactCells : ruledOnlyExact;
    const result = verdict(withRows(cells, () => ({ ...json404(), body: Buffer.from(text) })));
    hold(result.passed && cells.every(cell => receiptOf(result, cell)?.verdict === 'pass'), 'L3.09-local-ruled-404-request-id');
  }
  const listing =Buffer.from('<html><title>Index of /omega/console/assets/</title><a href="app-Ab12Cd34.js">app-Ab12Cd34.js</a></html>');
  for (const cell of exactCells) {
    for (const response of [spaDocument(), { status: 200, headers: { 'content-type': outcomes.asset.contentType,
      'cache-control': outcomes.asset.cacheControl }, body: assetBytes['immutable-js'] },
    { status: 200, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' }, body: listing }])
      hold(red(swap(cell.id, observe(cell, response)), 'L3.09-ruled-404-status'), 'L3.09-local-red-ruled-404-served');
    // A 404 carrying a file body or a listing, or any non-canonical 404, is not the exact 404.
    const not404: FixtureResponse[] = cell.method === 'HEAD'
      ? [header('content-type', 'text/html; charset=utf-8')(json404()), header('cache-control', 'no-cache')(json404()), header('x-content-type-options')(json404())]
      : [{ ...json404(), body: documentBytes }, { ...json404(), body: listing }, { ...json404(), body: assetBytes['immutable-js'] },
        header('content-type', 'text/html; charset=utf-8')(json404())];
    for (const response of not404) hold(red(swap(cell.id, observe(cell, response)), 'L3.09-ruled-404-contract'), 'L3.09-local-red-ruled-404-body');
    if (cell.method === 'GET') for (const text of jsonNot404)
      hold(red(swap(cell.id, observe(cell, { ...json404(), body: Buffer.from(text) })), 'L3.09-ruled-404-contract'), 'L3.09-local-red-ruled-404-json');
    if (cell.method === 'HEAD') {
      hold(red(swap(cell.id, observe(cell, json404(), cell.target!, Buffer.from('{}'))), 'L3.09-head-body'), 'L3.09-local-red-ruled-404-head-body');
      hold(red(swap(cell.id, observe(cell, { ...json404(), status: 200 })), 'L3.09-get-head-status'), 'L3.09-local-red-ruled-404-head-disagree');
    }
  }

  // Ruling e: the exact 404 OR the SPA fallback (200, GET body = the bound document); anything else fails.
  const caseCells = ['upper-case-path', 'upper-case-console'].flatMap(ruledCells);
  const caseSpa = verdict(withRows(caseCells, () => spaDocument()));
  hold(caseCells.length === 8 && caseCells.every(cell => receiptOf(restoredVerdict, cell)?.verdict === 'pass')
    && caseSpa.passed && caseCells.every(cell => receiptOf(caseSpa, cell)?.verdict === 'pass'), 'L3.09-local-ruled-404-or-spa-restored');
  // Its 200 branch needs the bound document: unbound (or empty) => the cells are capped at BLOCKED, never 'pass'.
  for (const bound of [noDocument, emptyDocument]) {
    const result = verdict(restored, bound);
    hold(caseCells.every(cell => receiptOf(result, cell)?.verdict === 'blocked'), 'L3.09-local-ruled-404-or-spa-blocked');
  }
  // The 200 branch is the contract's letter only (status 200, GET body = the bound document, HEAD
  // no body and Content-Length = its byteLength). Every other canonical SPA header is RECORDED by name, never failed:
  // the exact document with 'Cache-Control: no-store' (stricter) or without a CSP passes.
  const spaRecord = (cell: ConsoleOriginCell, nonCanonicalHeaders: readonly string[]) =>
    JSON.stringify({ cell: cell.id, verdict: 'pass', status: 200, nonCanonicalHeaders });
  for (const [change, names] of [[(response: FixtureResponse) => response, []], [header('cache-control', 'no-store'), ['cache-control']],
    [header('content-security-policy'), ['content-security-policy']], [header('content-type', outcomes.asset.contentType), ['content-type']],
    [header('content-type', 'text/html'), ['content-type']],
    [(response: FixtureResponse) => header('content-security-policy', "frame-ancestors 'none'")(header('cache-control', 'private')(
      header('content-type', 'text/plain')(response))), ['content-type', 'cache-control', 'content-security-policy']]] as const) {
    const result = verdict(withRows(caseCells, () => change(spaDocument())));
    hold(result.passed && caseCells.every(cell => JSON.stringify(receiptOf(result, cell)) === spaRecord(cell, names)),
      'L3.09-local-ruled-404-or-spa-recorded');
  }
  // Its 404 branch is ruling d's exact 404: the gateway's requestId is allowed, any other JSON member is not.
  const caseNotFound = verdict(withRows(caseCells, () => ({ ...json404(), body: Buffer.from(`{"error":"not_found","requestId":"${notFoundId}"}`) })));
  hold(caseNotFound.passed && caseCells.every(cell => JSON.stringify(receiptOf(caseNotFound, cell)) === JSON.stringify({ cell: cell.id, verdict: 'pass' })),
    'L3.09-local-ruled-404-or-spa-request-id');
  for (const cell of caseCells.filter(row => row.method === 'GET')) for (const text of jsonNot404)
    hold(red(swap(cell.id, observe(cell, { ...json404(), body: Buffer.from(text) })), 'L3.09-ruled-404-or-spa-contract'),
      'L3.09-local-red-ruled-404-or-spa-json');
  for (const cell of caseCells) {
    const reds: [FixtureResponse, string][] = [
      [{ ...spaDocument(), status: 302 }, 'L3.09-ruled-404-or-spa-status'], [{ ...json404(), status: 403 }, 'L3.09-ruled-404-or-spa-status'],
      [{ ...json404(), status: 500 }, 'L3.09-ruled-404-or-spa-status'],
      [header('content-type', 'text/html; charset=utf-8')(json404()), 'L3.09-ruled-404-or-spa-contract'],
      [header('cache-control', 'no-cache')(json404()), 'L3.09-ruled-404-or-spa-contract'],
    ];
    if (cell.method === 'GET') reds.push([{ ...spaDocument(), body: Buffer.from('<!doctype html><title>Other</title>') }, 'L3.09-ruled-404-or-spa-document'],
      [{ ...spaDocument(), body: assetBytes['immutable-js'] }, 'L3.09-ruled-404-or-spa-document'],
      [{ ...spaDocument(), body: Buffer.alloc(0) }, 'L3.09-ruled-404-or-spa-document'],
      [{ ...json404(), body: documentBytes }, 'L3.09-ruled-404-or-spa-contract']);
    else reds.push([header('content-length', String(documentBytes.length + 1))(spaDocument()), 'L3.09-ruled-404-or-spa-contract'],
      [json404(), 'L3.09-get-head-status']);
    for (const [response, check] of reds) {
      // HEAD's GET partner answers the SPA fallback, so a HEAD 404 disagrees with it.
      const rows = cell.method === 'HEAD' && check === 'L3.09-get-head-status' ? withRows(caseCells.filter(row => row.id !== cell.id), () => spaDocument()) : restored;
      hold(red(rows.map(row => row.cell === cell.id ? observe(cell, response) : row), check), 'L3.09-local-red-ruled-404-or-spa');
    }
  }

  // ---- REBIND 46e39a4686: files-only vectors, the full 404 body, ONE cell per ruled spelling ----
  // (a) Every copied files-only vector (231-253) is planned verbatim, GET+HEAD x both accepts (255-266), and sent.
  const filesOnly = sent.filter(cell => cell.kind === 'files-only');
  hold(filesOnly.length === 80 && CANON.filesOnlyStaticPaths.every((path, index) => CANON.methods.every(method => CANON.accepts.every(accept =>
    filesOnly.some(cell => cell.id === `files-only#${index}|${method}|${accept}` && cell.target === path && cell.gap === null
      && cell.slot === null && cell.pair === `files-only#${index}|${accept}`)))), 'L3.09-local-files-only-cells');
  // (b) Request vectors only: J00 never plants, binds or reads a fixture file (52-65). A binding with nothing but the listener
  // url plans the same 80 cells, and no binding input naming files changes the verdict.
  hold(JSON.stringify(planConsoleOriginMatrix({ url: binding.url }).filter(cell => cell.kind === 'files-only'))
    === JSON.stringify(plan.filter(cell => cell.kind === 'files-only'))
    && [{ files: { '.env': 'NOT_A_SECRET=fixture\n' } }, { fixtures: ['/omega/console/.vite/manifest.json'] },
      { filesOnlyStaticPaths: ['/omega/console/x'] }, { consoleDir: '/tmp/build' }]
      .every(extra => JSON.stringify(verdict(restored, { ...binding, ...extra })) === JSON.stringify(restoredVerdict)),
  'L3.09-local-files-only-request-only');
  // (c) ONE cell per spelling: exactly 'dotfile' and 'assets-directory' (both exact-404) are ruled spellings that are also a
  // canonical static vector on a canonical method; each is planned once, as files-only#0 / files-only#10, carrying the ruling.
  // Any other overlap (a new canonical vector or ruling) turns this red until someone decides it.
  const staticVectors = [...CANON.absentStaticPaths, ...CANON.malformedStaticPaths, ...CANON.filesOnlyStaticPaths];
  const overlaps = CONSOLE_ORIGIN_RULED.filter(ruled => ruled.methods.some(method => CANON.methods.includes(method))
    && staticVectors.includes(ruled.target));
  const mergedIds = ['files-only#0', 'files-only#10'].flatMap(vector => CANON.methods.flatMap(method =>
    CANON.accepts.map(accept => `${vector}|${method}|${accept}`)));
  hold(JSON.stringify(overlaps.map(ruled => [ruled.id, ruled.ruling, CANON.filesOnlyStaticPaths.indexOf(ruled.target)]))
    === JSON.stringify([['dotfile', 'exact-404', 0], ['assets-directory', 'exact-404', 10]])
    && JSON.stringify(plan.filter(cell => cell.ruledId === 'dotfile' || cell.ruledId === 'assets-directory').map(cell => cell.id))
      === JSON.stringify(mergedIds)
    && plan.filter(cell => cell.ruledId !== null && cell.kind !== 'ruled').every(cell => cell.kind === 'files-only' && cell.ruling === 'exact-404')
    && !plan.some(cell => cell.kind === 'ruled' && staticVectors.includes(cell.target ?? '') && CANON.methods.includes(cell.method))
    && CONSOLE_ORIGIN_RULED.length === 12 && overlaps.every(ruled => mergedRuling(CANON, ruled.target) === ruled),
  'L3.09-local-ruled-canonical-overlap');
  // (d) The merged cell holds the STRICTER expectation: {"error":"not_found"} (ruling d's minimum, and the whole 24a61e0424
  // contract) is red on it by the canonical half alone, while it stays green on the 16 ruled-only exact-404 cells; a listing
  // in a 404 is red by BOTH halves (both predicates run).
  const mergedCells = filesOnly.filter(cell => cell.ruling === 'exact-404');
  const bareNotFound = (): FixtureResponse => ({ ...json404(), body: Buffer.from(JSON.stringify({ error: outcomes.notFound.error })) });
  hold(mergedCells.length === 8 && mergedCells.every(cell => mergedIds.includes(cell.id))
    && verdict(withRows(ruledOnlyExact, bareNotFound)).passed, 'L3.09-local-merged-404-stricter');
  for (const cell of mergedCells.filter(row => row.method === 'GET')) {
    const bare = verdict(swap(cell.id, observe(cell, bareNotFound())));
    hold(!bare.passed && bare.failures.includes('L3.09-not-found-contract') && !bare.failures.includes('L3.09-ruled-404-contract'),
      'L3.09-local-merged-404-stricter');
    const listed = verdict(swap(cell.id, observe(cell, { ...json404(), body: listing })));
    hold(['L3.09-not-found-contract', 'L3.09-ruled-404-contract'].every(check => listed.failures.includes(check)), 'L3.09-local-merged-404-both');
  }
  // (e) The full 404 body (282-293) on every canonical 404 kind (absent, malformed, files-only). The pattern and key set are the
  // canonical ones and equal J00's trace-id form (REQUEST_ID, used by rulings b/d/e).
  const canonical404 = sent.filter(cell => ['absent', 'malformed', 'files-only'].includes(cell.kind));
  hold(canonical404.length === 67 * 4 && outcomes.notFound.requestId === REQUEST_ID.source && Array.isArray(outcomes.notFound.keys)
    && JSON.stringify([...outcomes.notFound.keys].sort()) === '["error","requestId"]' && REQUEST_ID.test(notFoundId), 'L3.09-local-request-id-form');
  // The gateway's bytes, reordered or spaced, hold on all of them at once.
  for (const text of [`{"requestId":"${notFoundId}","error":"not_found"}`, ` { "error" : "not_found" , "requestId" : "${notFoundId}" }\n`,
    '{"error":"not_found","requestId":"abcdefabcdefabcdefabcdefabcdefab"}']) {
    const result = verdict(withRows(canonical404, () => ({ ...json404(), body: Buffer.from(text) })));
    hold(result.passed && canonical404.every(cell => receiptOf(result, cell)?.verdict === 'pass'), 'L3.09-local-not-found-body-restored');
  }
  // The 24a61e0424 body (no requestId) is red on EVERY canonical 404 GET cell.
  for (const cell of canonical404.filter(row => row.method === 'GET'))
    hold(red(swap(cell.id, observe(cell, bareNotFound())), 'L3.09-not-found-contract'), 'L3.09-local-red-not-found-no-request-id');
  // Planted fixture bytes (54, 58, 63, 65): negative mutants only.
  const planted = ['NOT_A_SECRET=fixture\n', 'dot dir', '{}', '1'];
  const not404Bodies = [`{"error":"not_found","requestId":"${notFoundId}","path":"/omega/console/.env"}`,
    `{"error":"not_found","requestId":"${notFoundId}","ok":true}`, `{"error":"not_found","requestId":"${notFoundId.toUpperCase()}"}`,
    `{"error":"not_found","requestId":"${notFoundId.slice(1)}"}`, `{"error":"not_found","requestId":"${notFoundId}0"}`,
    '{"error":"not_found","requestId":12345}', '{"error":"not_found","requestId":true}', '{"error":"not_found","requestId":""}',
    // A member twice: JSON.parse keeps only the last copy, so the source's own check would pass these; the raw read refuses.
    `{"error":"not_found","requestId":"${notFoundId}","requestId":"${notFoundId}"}`,
    `{"error":"root:x:0:0:root:/root:/bin/sh","error":"not_found","requestId":"${notFoundId}"}`,
    `{"requestId":"${notFoundId}"}`, `{"error":"other","requestId":"${notFoundId}"}`, `{"error":"not\\u005ffound","requestId":"${notFoundId}"}`,
    `{"error":"not_found","requestId":"${notFoundId}"}{"error":"not_found"}`, `{"error":{"code":"not_found"},"requestId":"${notFoundId}"}`,
    ...planted];
  const bodyCells = [...filesOnly.filter(cell => cell.method === 'GET'), first('absent'), first('malformed')];
  for (const cell of bodyCells) for (const text of not404Bodies)
    hold(red(swap(cell.id, observe(cell, { ...json404(), body: Buffer.from(text) })), 'L3.09-not-found-contract'), 'L3.09-local-red-not-found-body');
  // A planted file (or a listing) served, at 200: red on every files-only cell; a HEAD 404 trailed by its bytes: red.
  for (const cell of filesOnly) {
    for (const bytes of [...planted.map(text => Buffer.from(text)), listing])
      hold(red(swap(cell.id, observe(cell, { status: 200, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-cache' },
        body: bytes })), 'L3.09-not-found-status'), 'L3.09-local-red-files-only-served');
    if (cell.method === 'HEAD')
      hold(red(swap(cell.id, observe(cell, json404(), cell.target!, Buffer.from(planted[0]))), 'L3.09-head-body'), 'L3.09-local-red-files-only-head-body');
  }
  mutate(first('files-only', 'HEAD'), header('cache-control', 'no-cache'), 'L3.09-not-found-contract');
  mutate(first('files-only'), header('content-type', 'text/plain; charset=utf-8'), 'L3.09-not-found-contract');
  return [...broken];
}
