import { useCallback, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useUpdateSharedLink } from "src/api/shared-links";
import { useGenerationReset } from "./useDialogGeneration";
import { readAccessMode } from "./shareLinkHelpers";

// Who can open the link. Only a mode the server confirmed is shown as
// selected; a requested change stays pending until the server answers.
export default function useShareAccess({
  shareLink,
  linksUpdatedAt,
  linkBlocked,
  refetch,
  generation,
}) {
  // Access mode requested but not yet acknowledged by the server.
  const [pendingMode, setPendingMode] = useState(null);
  // Access mode acknowledged by a PATCH response, before the list refetch lands.
  const [ackMode, setAckMode] = useState(null); // { linkId, mode, at }
  // A failed update left the server state uncertain until a successful reread.
  const [accessUnknown, setAccessUnknown] = useState(false);
  const [accessNotice, setAccessNotice] = useState(null);
  const updateMutation = useUpdateSharedLink();

  useGenerationReset(generation, () => {
    setPendingMode(null);
    setAckMode(null);
    setAccessUnknown(false);
    setAccessNotice(null);
    updateMutation.reset?.();
  });

  // A matching PATCH acknowledgement wins until a newer server read lands;
  // otherwise the server read is authoritative. Without either, the mode is
  // not known and nothing is shown as selected.
  const serverMode = readAccessMode(shareLink);
  const confirmedMode = useMemo(() => {
    if (ackMode && shareLink?.id && ackMode.linkId === shareLink.id) {
      const serverIsNewer =
        typeof linksUpdatedAt === "number" && linksUpdatedAt > ackMode.at;
      if (!serverIsNewer) return ackMode.mode;
    }
    return serverMode;
  }, [ackMode, shareLink, linksUpdatedAt, serverMode]);

  const settling = accessUnknown || Boolean(pendingMode);
  const ready = Boolean(shareLink?.id) && !linkBlocked && !settling;

  // A successful reread settles any doubt left by a failed update.
  const clearUnknown = useCallback(() => {
    setAccessUnknown(false);
    setAccessNotice(null);
  }, []);

  // Persist access mode changes to server; display only confirmed state.
  const changeMode = useCallback(
    (mode) => {
      if (!ready || mode === confirmedMode) return;
      const linkId = shareLink.id;
      const gen = generation.current;
      setPendingMode(mode);
      setAccessNotice(null);
      updateMutation.mutate(
        { id: linkId, access_type: mode },
        {
          onSuccess: (response) => {
            if (gen !== generation.current) return;
            const confirmed = readAccessMode(response?.data?.result) || mode;
            setAckMode({ linkId, mode: confirmed, at: Date.now() });
            setPendingMode(null);
            setAccessUnknown(false);
          },
          onError: async () => {
            if (gen !== generation.current) return;
            // The request may have committed before the response was lost:
            // never assert a rollback, reread instead.
            setPendingMode(null);
            setAccessUnknown(true);
            setAccessNotice(
              "Could not confirm the access change. Rechecking the current setting…",
            );
            const result = await refetch();
            if (gen !== generation.current) return;
            if (result) {
              setAckMode(null);
              setAccessUnknown(false);
              setAccessNotice(null);
              enqueueSnackbar(
                "Couldn't confirm the access change. Showing the current setting.",
                { variant: "warning" },
              );
            } else {
              setAccessNotice(
                "The access setting couldn't be confirmed. Recheck to continue.",
              );
            }
          },
        },
      );
    },
    [ready, confirmedMode, shareLink, updateMutation, refetch, generation],
  );

  return {
    confirmedMode,
    pendingMode,
    accessUnknown,
    // A change is in flight or its outcome is unknown.
    settling,
    // The link is usable and its access setting is settled.
    ready,
    notice:
      accessUnknown && !pendingMode && accessNotice
        ? { message: accessNotice, action: "Recheck" }
        : null,
    changeMode,
    clearUnknown,
  };
}
