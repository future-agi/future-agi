// Secrets by reference: a secret is given as a pointer into the team's secret
// manager (vault://acme/sim/openai_api_key), never as the value. These checks
// run in the browser before anything would be sent.

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

export const isSecretRef = (s) => REF_RE.test(String(s || "").trim());

export const storeOfRef = (ref) => {
  const scheme = String(ref || "").split("://")[0];
  return SECRET_STORES.find((s) => s.scheme === scheme) || null;
};

// Provider prefixes first (OpenAI, Stripe, AWS, GitHub, Slack, GitLab), then a
// JWT, then anything long, unbroken and mixed enough to be a token. A masked
// value copied off a screen counts too.
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

export const validateSecretRef = (input) => {
  const v = String(input || "").trim();
  if (!v) return { ok: false, error: "Paste a reference to the secret." };
  if (looksLikeRawKey(v)) return { ok: false, error: RAW_KEY_MESSAGE };
  if (!isSecretRef(v)) {
    return {
      ok: false,
      error:
        "Use a secret-manager reference, for example vault://acme/sim/openai_api_key.",
    };
  }
  return { ok: true, ref: v };
};

export const MAX_EGRESS_DOMAINS = 20;

export const parseEgress = (text) => [
  ...new Set(
    String(text || "")
      .split(/[\s,]+/)
      .map((d) => d.trim().toLowerCase())
      .filter(Boolean),
  ),
];

// A hostname only: no scheme, path or port. Returns the reason when not.
export const egressError = (domains) => {
  const bad = domains.filter(
    (d) => !/^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(d),
  );
  if (bad.length) {
    return `${bad.join(", ")} ${bad.length === 1 ? "isn't a hostname" : "aren't hostnames"}. Leave out the scheme, path and port.`;
  }
  if (domains.length > MAX_EGRESS_DOMAINS) {
    return `${domains.length} domains. The sandbox allows at most ${MAX_EGRESS_DOMAINS}.`;
  }
  return null;
};
