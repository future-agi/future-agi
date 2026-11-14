import { useCallback, useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useGenerationReset } from "./useDialogGeneration";

const COPIED_RESET_MS = 2000;

// Copies the share URL one write at a time and reports success only after
// the clipboard write resolves for the session that started it.
export default function useCopyLink({ url, ready, generation }) {
  const [copied, setCopied] = useState(false);
  const copyInFlight = useRef(false);
  const copiedTimer = useRef(null);

  const clearCopiedTimer = useCallback(() => {
    if (copiedTimer.current) {
      clearTimeout(copiedTimer.current);
      copiedTimer.current = null;
    }
  }, []);

  useGenerationReset(generation, () => {
    copyInFlight.current = false;
    clearCopiedTimer();
    setCopied(false);
  });

  useEffect(() => clearCopiedTimer, [clearCopiedTimer]);

  const copy = useCallback(async () => {
    if (!ready || copyInFlight.current) return;
    const gen = generation.current;
    if (typeof navigator.clipboard?.writeText !== "function") {
      enqueueSnackbar(
        "Clipboard isn't available. Select the link and copy it manually.",
        { variant: "warning" },
      );
      return;
    }
    copyInFlight.current = true;
    try {
      await navigator.clipboard.writeText(url);
      if (gen !== generation.current) return;
      setCopied(true);
      enqueueSnackbar("Link copied!", {
        variant: "success",
        autoHideDuration: 1500,
      });
      clearCopiedTimer();
      copiedTimer.current = setTimeout(() => {
        copiedTimer.current = null;
        setCopied(false);
      }, COPIED_RESET_MS);
    } catch {
      if (gen !== generation.current) return;
      enqueueSnackbar(
        "Couldn't copy the link. Select it and copy it manually.",
        { variant: "warning" },
      );
    } finally {
      if (gen === generation.current) copyInFlight.current = false;
    }
  }, [ready, url, generation, clearCopiedTimer]);

  return { copied, copy };
}
