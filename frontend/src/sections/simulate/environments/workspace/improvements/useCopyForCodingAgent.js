import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { copyToClipboard } from "src/utils/copyToClipboard";
import { fixErrorOf } from "src/api/simulate-environments/fixRuns";
import {
  codingAgentBriefKey,
  codingAgentPrompt,
  getCodingAgentBrief,
} from "src/api/simulate-environments/codingAgent";

// The brief says how many attempts are left, so it is read again after a short while.
const BRIEF_FRESH_MS = 30 * 1000;
const COPIED_MS = 2000;

const setupUrl = () =>
  `${window.location.origin}${paths.dashboard.settings.mcpServer}`;

// "Copy for coding agent" on a finished run: reads the brief, copies the setup and the
// brief's instructions as one prompt, and says so. A blocked brief copies nothing and
// shows the platform's reason. `prefetch` reads the brief ahead of the click (a menu
// opening, a hover), so the copy itself needs no wait.
export default function useCopyForCodingAgent(sourceRunId) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const query = {
    queryKey: codingAgentBriefKey(sourceRunId),
    queryFn: () => getCodingAgentBrief(sourceRunId),
    staleTime: BRIEF_FRESH_MS,
    retry: false,
  };

  const prefetch = () => {
    if (sourceRunId) queryClient.prefetchQuery(query);
  };

  const copy = async () => {
    if (!sourceRunId || busy) return;
    setBusy(true);
    try {
      const brief = await queryClient.fetchQuery(query);
      if (brief.blocked) {
        enqueueSnackbar(brief.blocked.message, { variant: "warning" });
        return;
      }
      const ok = await copyToClipboard(codingAgentPrompt(brief, setupUrl()));
      if (!ok) {
        enqueueSnackbar(
          "Your browser did not allow the copy. Allow clipboard access for this site, then try again.",
          { variant: "error" },
        );
        return;
      }
      enqueueSnackbar("Copied. Paste it into your coding agent.", {
        variant: "success",
      });
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), COPIED_MS);
    } catch (e) {
      enqueueSnackbar(
        fixErrorOf(e).message ||
          "Could not read the brief for your coding agent. Try again.",
        { variant: "error" },
      );
    } finally {
      setBusy(false);
    }
  };

  return { copy, prefetch, copied, busy };
}
