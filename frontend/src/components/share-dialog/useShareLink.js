import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCreateSharedLink, useGetSharedLinks } from "src/api/shared-links";
import { useGenerationReset } from "./useDialogGeneration";
import { describeLinkError, isLinkActive, readToken } from "./shareLinkHelpers";

// The share link for one resource: reads the server on every open, creates a
// restricted link only after a fresh empty read, and offers Retry/Recheck.
export default function useShareLink({
  open,
  resourceType,
  resourceId,
  generation,
}) {
  // Reopen/resource switch rereads the server before enabling actions.
  const [verifying, setVerifying] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  // Result of the server read that completed in a generation. Auto-create is
  // only allowed once the current generation has its own fresh, empty read:
  // a cached empty list from an earlier open is not discovery.
  const [discovery, setDiscovery] = useState({ gen: -1, empty: false });
  // When the link created in this session was first observed; a newer
  // successful server read that lacks it supersedes the mutation result.
  const [createdSeenAt, setCreatedSeenAt] = useState(null);
  const autoCreated = useRef(false);
  const loadingSeenGen = useRef(-1);
  const verifiedGen = useRef(-1);

  const {
    data: links,
    isLoading: linksLoading,
    isError: linksError,
    error: linksErrorDetail,
    dataUpdatedAt: linksUpdatedAt,
    isFetching: linksFetching,
    refetch: refetchLinks,
  } = useGetSharedLinks(open ? resourceType : null, open ? resourceId : null);
  const createMutation = useCreateSharedLink();

  const activeLink = useMemo(() => {
    if (!links || !Array.isArray(links)) return null;
    return links.find((l) => isLinkActive(l)) || null;
  }, [links]);
  const createdLink = createMutation.data?.data?.result || null;
  const createdSuperseded =
    createdSeenAt !== null &&
    !linksError &&
    Array.isArray(links) &&
    typeof linksUpdatedAt === "number" &&
    linksUpdatedAt > createdSeenAt;
  const shareLink =
    activeLink || (createdLink && !createdSuperseded ? createdLink : null);
  const createError = Boolean(createMutation.isError);
  const creating = Boolean(createMutation.isPending);

  const refetch = useCallback(async () => {
    if (typeof refetchLinks !== "function") return null;
    const gen = generation.current;
    try {
      const result = await refetchLinks();
      if (!result || result.isError || result.data === undefined) return null;
      if (gen === generation.current) {
        setDiscovery({
          gen,
          empty: Array.isArray(result.data) && result.data.length === 0,
        });
      }
      return result;
    } catch {
      return null;
    }
  }, [refetchLinks, generation]);

  useEffect(() => {
    setCreatedSeenAt(createdLink ? Date.now() : null);
  }, [createdLink]);

  useGenerationReset(generation, ({ contextChanged }) => {
    autoCreated.current = false;
    // A query load already in progress for the new context belongs to the
    // new generation, whichever effect observed it first.
    loadingSeenGen.current = linksLoading ? generation.current : -1;
    setVerifying(false);
    setRetryBusy(false);
    // A create still in flight on close is kept (same resource): reopen then
    // waits for it instead of reading an empty list and creating again.
    if (contextChanged || !creating) createMutation.reset?.();
  });

  // A query load that started and finished in this generation is a fresh read.
  useEffect(() => {
    if (!open) return;
    if (linksLoading) {
      loadingSeenGen.current = generation.current;
      return;
    }
    if (
      loadingSeenGen.current === generation.current &&
      !linksError &&
      links !== undefined
    ) {
      loadingSeenGen.current = -1;
      setDiscovery({
        gen: generation.current,
        empty: Array.isArray(links) && links.length === 0,
      });
    }
  }, [open, linksLoading, linksError, links, generation]);

  // Each open (and each resource switch) rereads the server once before
  // trusting a cached link. A first load that is still running is already
  // that read.
  useEffect(() => {
    if (!open || !resourceType || !resourceId) return;
    if (verifiedGen.current === generation.current) return;
    verifiedGen.current = generation.current;
    if (linksLoading || links === undefined) return;
    if (typeof refetchLinks !== "function") return;
    const gen = generation.current;
    setVerifying(true);
    refetch().finally(() => {
      if (gen === generation.current) setVerifying(false);
    });
  }, [
    open,
    resourceType,
    resourceId,
    linksLoading,
    links,
    refetchLinks,
    refetch,
    generation,
  ]);

  // Auto-create a restricted shared link when dialog opens and none exists.
  // Only after a successful empty discovery in this generation; never from
  // an error state or a cached list left over from an earlier open.
  useEffect(() => {
    if (!open || !resourceType || !resourceId) return;
    if (discovery.gen !== generation.current || !discovery.empty) return;
    if (
      !linksLoading &&
      !linksFetching &&
      !linksError &&
      links &&
      links.length === 0 &&
      !createMutation.isPending &&
      !createMutation.isError &&
      !autoCreated.current
    ) {
      autoCreated.current = true;
      createMutation.mutate({
        resource_type: resourceType,
        resource_id: resourceId,
        access_type: "restricted",
      });
    }
  }, [
    open,
    links,
    linksLoading,
    linksFetching,
    linksError,
    resourceType,
    resourceId,
    createMutation,
    retryNonce,
    discovery,
    generation,
  ]);

  // Rereads the list; `onReread` runs once a read for this session succeeds.
  const retry = useCallback(
    async (onReread) => {
      if (retryBusy) return;
      const gen = generation.current;
      setRetryBusy(true);
      try {
        if (createMutation.isError) createMutation.reset?.();
        const result = await refetch();
        if (gen !== generation.current) return;
        if (!result) return;
        onReread?.();
        if (Array.isArray(result.data) && result.data.length === 0) {
          autoCreated.current = false;
          setRetryNonce((n) => n + 1);
        }
      } finally {
        if (gen === generation.current) setRetryBusy(false);
      }
    },
    [retryBusy, createMutation, refetch, generation],
  );

  const token = readToken(shareLink);
  const tokenUrl = token ? `${window.location.origin}/shared/${token}` : null;
  const loading = linksLoading || creating || verifying;
  const linkUnavailable =
    !loading &&
    !linksError &&
    !createError &&
    !shareLink &&
    Array.isArray(links) &&
    (links.length > 0 || Boolean(createdLink));

  let notice = null;
  if (linksError) {
    notice = {
      message: describeLinkError(linksErrorDetail, "load"),
      action: "Retry",
    };
  } else if (createError) {
    notice = {
      message: describeLinkError(createMutation.error, "create"),
      action: "Retry",
    };
  } else if (linkUnavailable) {
    notice = {
      message:
        "This share link is no longer active. Recheck to see the current state.",
      action: "Recheck",
    };
  }

  return {
    shareLink,
    tokenUrl,
    linksUpdatedAt,
    // Still reading or creating: nothing about the link is settled yet.
    loading,
    generating: linksLoading || creating,
    verifying,
    retryBusy,
    // The link can't be used until a read or create succeeds.
    blocked: loading || linksError || createError || retryBusy,
    notice,
    refetch,
    retry,
  };
}
