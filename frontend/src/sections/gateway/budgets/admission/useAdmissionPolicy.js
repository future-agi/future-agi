import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ledgerRefusal,
  readAdmissionPolicyState,
  writeAdmissionPolicy,
} from "../../providers/hooks/useOrgConfig";
import { admissionError, admissionStatus } from "./admissionPolicy";
import { CAPS_OFF_MESSAGE } from "./capsWording";

// Switching daily caps on needs the gateway to report its spend ledger
// "ready", and no gateway reports it yet, so the server refuses the switch.
// This is that refusal's wording, shown as it is.
export const LEDGER_MESSAGE = CAPS_OFF_MESSAGE;

// onRead (optional) hears every successful current read: the initial load,
// Reload, and the read after a write. Held in a ref so a new callback never
// starts another read.
export default function useAdmissionPolicy(
  organizationId,
  canEdit,
  onApplied,
  onRead,
) {
  const queryClient = useQueryClient();
  const latestOnRead = useRef(onRead);
  latestOnRead.current = onRead;
  // `ledger` is the spend-ledger status the same read reports (runtime state,
  // on the config or beside a null result), "unknown" until it is read.
  const [state, setState] = useState({
    loading: true,
    config: null,
    ledger: "unknown",
    error: null,
  });
  const [notice, setNotice] = useState(null);
  // A refusal for the spend ledger outranks the read after it, which may be
  // a cached "ready", until the next Reload or save.
  const [refusedLedger, setRefusedLedger] = useState(null);
  const [saving, setSaving] = useState(false);
  const alive = useRef(false);
  const busy = useRef(false);
  const request = useRef(null);
  const sequence = useRef(0);

  const reload = useCallback(async () => {
    const number = ++sequence.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState({ loading: true, config: null, ledger: "unknown", error: null });
    setRefusedLedger(null);
    try {
      const { config, ledger } = await readAdmissionPolicyState(
        organizationId,
        controller.signal,
      );
      if (!alive.current || number !== sequence.current) return undefined;
      setState({ loading: false, config, ledger, error: null });
      latestOnRead.current?.(config);
      return config;
    } catch (error) {
      if (alive.current && number === sequence.current)
        setState({ loading: false, config: null, ledger: "unknown", error });
      return undefined;
    }
  }, [organizationId]);

  useEffect(() => {
    alive.current = true;
    reload();
    return () => {
      alive.current = false;
      sequence.current += 1;
      request.current?.abort();
    };
  }, [reload]);

  // After a refusal for the spend ledger (nothing was saved, so the caller may
  // keep its draft) resolves to { refused, current }: current is the policy
  // read back after it, or undefined when that read failed. Else undefined.
  const save = async (draft, activate) => {
    if (!canEdit || busy.current || state.loading || state.error)
      return undefined;
    busy.current = true;
    setSaving(true);
    setNotice(null);
    setRefusedLedger(null);
    try {
      const result = await writeAdmissionPolicy(
        organizationId,
        state.config,
        draft,
        activate,
      );
      if (!alive.current) return undefined;
      setNotice({
        severity: result.gateway_synced === false ? "warning" : "success",
        text:
          result.gateway_synced === false
            ? result.gateway_warning ||
              "Saved, but gateway sync failed. Enforcement is not confirmed."
            : "Policy saved. Reloading the current server policy; saved state is not a live enforcement probe.",
      });
      const current = await reload();
      if (!alive.current) return undefined;
      if (current !== undefined) onApplied?.(current);
      for (const key of [
        "agentcc-org-config",
        "agentcc-org-config-history",
        "agentcc-gateway-config",
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    } catch (error) {
      if (!alive.current) return undefined;
      // The panel's ledger notice says why; no second, generic message.
      const refusal = ledgerRefusal(error);
      if (refusal === null) {
        const conflict = admissionStatus(error) === 409;
        setNotice({
          severity: "error",
          text: `${admissionError(error)} ${conflict ? "Stale draft discarded. Reloading the current policy; nothing is retried automatically." : "Reload the policy and review it before trying again."}`,
        });
      }
      // All unsuccessful writes are read back, including ambiguous transport
      // failures: never resubmit a possibly committed mutation automatically.
      const current = await reload();
      if (alive.current && current !== undefined) onApplied?.(current);
      if (alive.current && [401, 403].includes(admissionStatus(error))) {
        setState({ loading: false, config: null, error });
      }
      if (alive.current && refusal !== null) {
        setRefusedLedger(refusal);
        return { refused: refusal, current };
      }
    } finally {
      busy.current = false;
      if (alive.current) setSaving(false);
    }
    return undefined;
  };

  const ledger = refusedLedger ?? state.ledger;
  return { ...state, ledger, notice, saving, reload, save };
}
