/*
  Secrets by reference (PRD v6 AC-6.12, §1 preflight, AC-23.1b).

  A credential is captured as a pointer into the team's secret manager —
  `vault://acme/sim/vapi_api_key`, `aws-sm://prod/agents/openai` — never as
  the value. The reference can be shown, logged, exported and diffed without
  redaction; the value only ever exists inside the sandbox that resolves it.

  Changing a reference re-runs the access check (preflight) and nothing else:
  no rebuild, no new environment version. Which key a run used is not part of
  the world — what that key may do is, and the check reports it.
*/

export const SECRET_STORES = [
  { scheme: "vault", label: "HashiCorp Vault" },
  { scheme: "aws-sm", label: "AWS Secrets Manager" },
  { scheme: "gcp-sm", label: "GCP Secret Manager" },
  { scheme: "azure-kv", label: "Azure Key Vault" },
  { scheme: "op", label: "1Password" },
  { scheme: "doppler", label: "Doppler" },
];

const SCHEMES = SECRET_STORES.map((s) => s.scheme).join("|");
const REF_RE = new RegExp(
  `^(${SCHEMES})://[A-Za-z0-9._-]+(/[A-Za-z0-9._:-]+)+$`,
);

/** Whether a string is a secret-manager reference. */
export const isSecretRef = (s) => REF_RE.test(String(s || "").trim());

/** The store a reference points into — "HashiCorp Vault". */
export const storeOfRef = (ref) => {
  const scheme = String(ref || "").split("://")[0];
  return SECRET_STORES.find((s) => s.scheme === scheme) || null;
};

/*
  What a pasted secret looks like. Provider prefixes first (OpenAI, Stripe,
  AWS, GitHub, Slack, GitLab), then a JWT, then anything long, unbroken and
  mixed enough to be a token — and a masked value copied off a screen counts
  too, since it was a raw key before someone elided it.
*/
const RAW_PREFIX =
  /^(sk|pk|rk)[-_]|^sk-proj-|^AKIA[0-9A-Z]{8,}|^(ghp|gho|ghs|github_pat|glpat|xox[abprs])[-_]|^eyJ[\w-]+\.[\w-]+/;
export const looksLikeRawKey = (s) => {
  const v = String(s || "").trim();
  if (!v || isSecretRef(v)) return false;
  if (RAW_PREFIX.test(v)) return true;
  if (/[…•]/.test(v)) return true;
  return (
    !/\s|:\/\//.test(v) && v.length >= 20 && /[A-Za-z]/.test(v) && /\d/.test(v)
  );
};

export const RAW_KEY_MESSAGE =
  "That looks like a raw key. Store it in your secret manager and paste the reference.";

/*
  Demo: a connect form takes whatever key is typed so a walkthrough isn't
  stopped at the credential. What it records is still a reference — the
  slot the field names (its placeholder) — never the typed text.
*/
export const DEMO_ANY_KEY = true;
/** The reference a key field records: the reference typed, or in the demo the field's own slot. */
export const keyRefOf = (input, slot) => {
  const v = String(input || "").trim();
  if (!v) return "";
  if (isSecretRef(v)) return v;
  return DEMO_ANY_KEY && isSecretRef(slot) ? slot : "";
};

/** Accept a reference, or say plainly why not. */
export const validateSecretRef = (input) => {
  const v = String(input || "").trim();
  if (!v) return { ok: false, error: "Paste a reference to the secret." };
  if (looksLikeRawKey(v))
    return { ok: false, error: RAW_KEY_MESSAGE, raw: true };
  if (!isSecretRef(v))
    return {
      ok: false,
      error:
        "Use a secret-manager reference, for example vault://acme/sim/openai_api_key.",
    };
  return { ok: true, ref: v };
};

/** The workspace's default path for a secret — where a migrated value was moved to. */
export const defaultRefFor = (name) =>
  `vault://acme/sim/${String(name || "secret")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")}`;
