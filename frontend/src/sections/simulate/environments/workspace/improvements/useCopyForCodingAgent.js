import { useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";

import { paths } from "src/routes/paths";
import { copyToClipboard } from "src/utils/copyToClipboard";
import { fixErrorOf } from "src/api/simulate-environments/fixRuns";
import {
  codingAgentPrompt,
  getCodingAgentBrief,
} from "src/api/simulate-environments/codingAgent";

const COPIED_MS = 2000;

const setupUrl = () =>
  `${window.location.origin}${paths.dashboard.settings.mcpServer}`;

const statusOf = (error) => error?.response?.status ?? error?.statusCode;

function readFailure(error) {
  if (statusOf(error) === 404) {
    return "Future AGI has no brief for this run. The run may have been deleted. Reload the page and try again.";
  }
  return (
    fixErrorOf(error).message ||
    "Could not read the brief for your coding agent. Try again."
  );
}

// "Copy for coding agent" on a finished run: reads the brief fresh on each click (an
// attempt submitted from the CLI changes how many are left), copies the setup and
// the brief's instructions as one prompt, and says so. A blocked brief copies nothing
// and shows the platform's reason.
export default function useCopyForCodingAgent(sourceRunId) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    if (!sourceRunId || busy) return;
    setBusy(true);
    try {
      const brief = await getCodingAgentBrief(sourceRunId);
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
      enqueueSnackbar(readFailure(e), { variant: "error" });
    } finally {
      setBusy(false);
    }
  };

  return { copy, copied, busy };
}
