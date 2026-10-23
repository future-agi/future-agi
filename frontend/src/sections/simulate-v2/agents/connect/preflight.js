import { useEffect, useRef, useState } from "react";

/*
  Preflight — checking the source and every credential before an
  environment is built, as the product's connect panels do (Run preflight →
  "Ready to build" or "Credentials need attention"). A flow lists its
  checks; running them takes a moment; any edit to what was checked clears
  the result, so a stale green never builds.

  The prototype doesn't call anything: a reference that reads as rotated or
  revoked (…_old, expired, revoked) is the key a provider rejects.
*/
const REJECTED = /expired|revoked|rotated|_old\b|\bold_|invalid/i;

/** A credential, by its reference: missing, rejected by the provider, or verified. */
export const credentialCheck = ({ id, label, ref, provider }) => {
  if (!ref) return { id, label, ok: false, state: "missing", detail: "Not set" };
  if (REJECTED.test(ref)) return { id, label, ok: false, state: "failed", detail: `${provider || "The provider"} rejected it (401). Re-issue it, or point the reference at a current key.` };
  return { id, label, ok: true, state: "verified", detail: `${provider ? `Accepted by ${provider}` : "Resolved"} · ${ref}` };
};

/** Any other check: passes, or says what to fix. */
export const factCheck = ({ id, label, ok, okText, failText }) => ({ id, label, ok: !!ok, state: ok ? "verified" : "failed", detail: ok ? okText : failText });

/* E.164: the whole number, country code included, is at most 15 digits. */
export const phoneNumberError = (dial, number) => {
  const digits = String(number || "").replace(/[\s()-]/g, "");
  if (!digits) return "Add the number to call";
  if (!/^\d+$/.test(digits)) return "Digits only — the country code has its own field";
  const cc = String(dial || "").replace(/\D/g, "");
  if (digits.length < 6) return "Too short for a phone number";
  if (cc.length + digits.length > 15) return "Too long — E.164 numbers are at most 15 digits";
  return null;
};
export const e164Of = (dial, number) => `${dial || ""}${String(number || "").replace(/[\s()-]/g, "")}`;

/* The sandbox's egress allow-list is merged with the platform's own; past 20 domains it's refused at preflight. */
export const MAX_EGRESS_DOMAINS = 20;
export const parseEgress = (text) => [...new Set(String(text || "").split(/[\s,]+/).map((d) => d.trim().toLowerCase()).filter(Boolean))];
export const egressCheck = (domains) => {
  const bad = domains.filter((d) => !/^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(d));
  if (bad.length) return factCheck({ id: "egress", label: "Egress domains", ok: false, failText: `${bad.join(", ")} ${bad.length === 1 ? "isn't a hostname" : "aren't hostnames"} — no scheme, path or port.` });
  if (domains.length > MAX_EGRESS_DOMAINS) return factCheck({ id: "egress", label: "Egress domains", ok: false, failText: `${domains.length} domains — the sandbox allows at most ${MAX_EGRESS_DOMAINS}.` });
  return factCheck({ id: "egress", label: "Egress domains", ok: true, okText: `${domains.length} allowed: ${domains.join(", ")} — everything else is blocked while the agent runs.` });
};

/**
 * Run a flow's checks: `checks` are what to verify now, `key` is what they
 * were computed from — when it changes, the last result is cleared.
 *   → { status: "idle" | "running" | "done", results, ready, run }
 */
export function usePreflight(checks, key) {
  const [state, setState] = useState({ status: "idle", results: null });
  const latest = useRef(checks);
  latest.current = checks;
  const keyStr = JSON.stringify(key);
  const timer = useRef(null);
  useEffect(() => {
    clearTimeout(timer.current);
    setState({ status: "idle", results: null });
  }, [keyStr]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const run = () => {
    setState({ status: "running", results: null });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState({ status: "done", results: latest.current }), 1100);
  };
  const ready = state.status === "done" && (state.results || []).every((c) => c.ok);
  return { ...state, ready, run };
}
