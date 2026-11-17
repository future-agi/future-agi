// Every Omega Hub API path the app calls (SPEC §9). This is the one file that
// spells them; when the integrator regenerates api_contracts, only this file
// moves to the generated `apiPath` helpers.

export const HUB_API = "/omega-hub/v1";

const seg = (value) => encodeURIComponent(String(value ?? ""));
const filePath = (path) => String(path).split("/").map(seg).join("/");
const entryBase = (publisher, name) =>
  `${HUB_API}/entries/${seg(publisher)}/${seg(name)}`;
const versionBase = (publisher, name, version) =>
  `${entryBase(publisher, name)}/versions/${seg(version)}`;
const R = `${HUB_API}/review`;

export const hubApi = {
  // Public reads (SPEC §9.2)
  entries: `${HUB_API}/entries`,
  entry: entryBase,
  versions: (p, n) => `${entryBase(p, n)}/versions`,
  version: versionBase,
  versionBundle: (p, n, v) => `${versionBase(p, n, v)}/bundle`,
  versionFile: (p, n, v, path) =>
    `${versionBase(p, n, v)}/files/${filePath(path)}`,
  remixes: (p, n) => `${entryBase(p, n)}/remixes`,
  publishers: `${HUB_API}/publishers`,
  publisher: (handle) => `${HUB_API}/publishers/${seg(handle)}`,
  publisherAvatar: (handle) => `${HUB_API}/publishers/${seg(handle)}/avatar`,
  picks: `${HUB_API}/picks`,
  tags: `${HUB_API}/tags`,
  rules: `${HUB_API}/rules`,

  // Signed in (SPEC §9.3)
  me: `${HUB_API}/me`,
  myTokens: `${HUB_API}/me/tokens`,
  myToken: (id) => `${HUB_API}/me/tokens/${seg(id)}`,
  myLikes: `${HUB_API}/me/likes`,
  emailVerification: `${HUB_API}/me/email-verification`,
  emailVerificationConfirm: `${HUB_API}/me/email-verification/confirm`,
  verificationRequest: (handle) =>
    `${HUB_API}/publishers/${seg(handle)}/verification-request`,
  submissions: `${HUB_API}/submissions`,
  submission: (id) => `${HUB_API}/submissions/${seg(id)}`,
  withdraw: (id) => `${HUB_API}/submissions/${seg(id)}/withdraw`,
  like: (p, n) => `${entryBase(p, n)}/like`,
  yank: (p, n, v) => `${versionBase(p, n, v)}/yank`,
  unlist: (p, n) => `${entryBase(p, n)}/unlist`,
  reports: `${HUB_API}/reports`,

  // Maintainers (SPEC §9.4)
  review: {
    queue: `${R}/queue`,
    submission: (id) => `${R}/submissions/${seg(id)}`,
    diff: (id) => `${R}/submissions/${seg(id)}/diff`,
    file: (id, path) => `${R}/submissions/${seg(id)}/files/${filePath(path)}`,
    start: (id) => `${R}/submissions/${seg(id)}/start`,
    takeOver: (id) => `${R}/submissions/${seg(id)}/take-over`,
    decision: (id) => `${R}/submissions/${seg(id)}/decision`,
    recheck: (id) => `${R}/submissions/${seg(id)}/recheck`,
    reports: `${R}/reports`,
    resolveReport: (id) => `${R}/reports/${seg(id)}/resolve`,
    entryAction: (p, n, action) =>
      `${R}/entries/${seg(p)}/${seg(n)}/${seg(action)}`,
    likers: (p, n) => `${R}/entries/${seg(p)}/${seg(n)}/likers`,
    yank: (p, n, v) => `${R}/versions/${seg(p)}/${seg(n)}/${seg(v)}/yank`,
    blocklist: `${R}/blocklist`,
    liftBlock: (id) => `${R}/blocklist/${seg(id)}/lift`,
    picks: `${R}/picks`,
    pick: (id) => `${R}/picks/${seg(id)}`,
    publishers: `${R}/publishers`,
    publisherAction: (handle, action) =>
      `${R}/publishers/${seg(handle)}/${seg(action)}`,
    audit: `${R}/audit`,
  },
};
