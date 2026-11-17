import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import { creditKeys, fetchCheckoutStatus } from "./api";

const POLL_MS = 1500;
const MAX_POLLS = 20;
const FOLLOW_UP_MS = 30_000;

// The return's parameters; the legacy ?checkout_session=<id> is a success.
function readReturn(params) {
  const legacy = params.get("checkout_session");
  return {
    topup: params.get("topup") || (legacy ? "success" : null),
    sessionId: params.get("session_id") || legacy,
  };
}

// The checkout's way back: Billing?topup=success&session_id=<id> or
// ?topup=cancel. On success it asks the ledger (checkout/<id>/) until the
// top-up is credited, then refreshes the balance. Either way it drops the
// two parameters and keeps the rest of the URL. Returns { confirming }: a
// success return is being confirmed while its parameters are in the URL.
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
    const { topup, sessionId } = readReturn(params);
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
    const refreshCredits = async () => {
      await queryClient.cancelQueries({ queryKey: creditKeys.all });
      if (stopped) return;
      await queryClient.invalidateQueries(
        { queryKey: creditKeys.all, refetchType: "all" },
        { throwOnError: true },
      );
    };
    const poll = async () => {
      polls += 1;
      let credited = false;
      let refreshAttempted = false;
      try {
        credited = !!(await fetchCheckoutStatus(sessionId))?.credited;
      } catch {
        credited = false;
      }
      if (stopped) return;
      if (credited) {
        refreshAttempted = true;
        try {
          await refreshCredits();
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
        // Pending is not a zero balance. Refresh even if the checkout status
        // has not caught up, but do not duplicate this poll's failed refresh.
        if (!refreshAttempted) {
          try {
            await refreshCredits();
          } catch {
            // A failed read must not turn the pending notice into success.
          }
        }
        if (stopped) return;
        announce(
          sessionId,
          "Your payment is still being processed. We will refresh the balance once more shortly. If it is still pending, refresh Billing later.",
          "info",
        );
        // Keep the return URL until this single slow follow-up completes:
        // clearing it now would clean up the effect and cancel the timer.
        timer = setTimeout(async () => {
          try {
            await refreshCredits();
          } catch {
            // Stay pending; no additional polling or success announcement.
          }
          if (!stopped) clear();
        }, FOLLOW_UP_MS);
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

  // The parameters stay until the top-up is credited or the single slow
  // follow-up has run, so this is true for exactly that long.
  const { topup, sessionId } = readReturn(new URLSearchParams(location.search));
  return { confirming: topup === "success" && !!sessionId };
}
