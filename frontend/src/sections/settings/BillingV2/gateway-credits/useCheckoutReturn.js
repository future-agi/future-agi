import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import { creditKeys, fetchCheckoutStatus } from "./api";

const POLL_MS = 1500;
const MAX_POLLS = 20;

// The checkout's way back: Billing?topup=success&session_id=<id> or
// ?topup=cancel. On success it asks the ledger (checkout/<id>/) until the
// top-up is credited, then refreshes the balance. Either way it drops the
// two parameters and keeps the rest of the URL.
export function useCheckoutReturn() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // What was already said, so a re-run (React's strict mode) says it once.
  const announced = useRef(new Set());
  const announce = (key, message, variant) => {
    if (announced.current.has(key)) return;
    announced.current.add(key);
    enqueueSnackbar(message, { variant });
  };

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const legacy = params.get("checkout_session");
    const topup = params.get("topup") || (legacy ? "success" : null);
    const sessionId = params.get("session_id") || legacy;
    if (!topup) return undefined;

    const clear = () => {
      params.delete("topup");
      params.delete("session_id");
      params.delete("checkout_session");
      const search = params.toString();
      navigate(
        { search: search ? `?${search}` : "", hash: location.hash },
        { replace: true },
      );
    };

    if (topup !== "success" || !sessionId) {
      announce("cancel", "Top-up cancelled. No payment was taken.", "info");
      clear();
      return undefined;
    }

    let stopped = false;
    let polls = 0;
    let timer;
    const poll = async () => {
      polls += 1;
      let credited = false;
      try {
        credited = !!(await fetchCheckoutStatus(sessionId))?.credited;
      } catch {
        credited = false;
      }
      if (stopped) return;
      if (credited) {
        try {
          await queryClient.cancelQueries({ queryKey: creditKeys.all });
          await queryClient.invalidateQueries(
            { queryKey: creditKeys.all, refetchType: "all" },
            { throwOnError: true },
          );
        } catch {
          credited = false;
        }
      }
      if (stopped) return;
      if (credited) {
        announce(
          sessionId,
          "Credits added to your AI Gateway balance.",
          "success",
        );
        clear();
      } else if (polls >= MAX_POLLS) {
        announce(
          sessionId,
          "Your payment is still being processed. The balance updates when it settles.",
          "info",
        );
        clear();
      } else {
        timer = setTimeout(poll, POLL_MS);
      }
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.hash, location.search, navigate, queryClient]);
}
