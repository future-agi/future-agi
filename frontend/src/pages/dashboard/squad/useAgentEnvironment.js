import { useEffect, useState } from "react";

// Keep revision reads tied to the current tenant projection and selection. An old
// response must never authorize a save after either one changes.
export default function useAgentEnvironment({
  client,
  result,
  draft,
  allowed,
  open,
}) {
  const [request, setRequest] = useState(null);
  const [retry, setRetry] = useState(0);
  const environments =
    result?.available && Array.isArray(result.environments)
      ? result.environments
      : [];
  const selected = environments.find(
    (entry) => entry.id === draft.environmentId,
  );
  const canSelect =
    allowed &&
    result?.available === true &&
    typeof client?.listEnvironmentRevisions === "function";
  const canRead = open && canSelect && Boolean(selected);

  useEffect(() => {
    if (!canRead) {
      setRequest(null);
      return undefined;
    }
    let cancelled = false;
    const identity = { client, result, id: draft.environmentId, retry };
    setRequest({ ...identity, loading: true });
    Promise.resolve()
      .then(() => client.listEnvironmentRevisions(identity.id))
      .then((response) => {
        if (cancelled) return;
        const matches =
          response?.available === true &&
          response.environmentId === identity.id;
        setRequest({
          ...identity,
          loading: false,
          revisions:
            matches && Array.isArray(response.revisions)
              ? response.revisions.filter(
                  (entry) =>
                    Number.isSafeInteger(entry.revision) && entry.revision > 0,
                )
              : [],
          error: matches
            ? ""
            : response?.reason ||
              "The environment revisions could not be verified. Try reading them again.",
        });
      })
      .catch(() => {
        if (!cancelled)
          setRequest({
            ...identity,
            loading: false,
            revisions: [],
            error:
              "Couldn't read environment revisions. Your draft is kept here.",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [canRead, client, result, draft.environmentId, retry]);

  const current =
    canRead &&
    request?.client === client &&
    request.result === result &&
    request.id === draft.environmentId &&
    request.retry === retry
      ? request
      : null;
  const revisions = current?.revisions || [];
  const exactRevision = revisions.find(
    (entry) => entry.revision === draft.environmentRevision,
  );
  const valid =
    !draft.environmentId ||
    Boolean(canSelect && selected?.status === "active" && exactRevision);
  return {
    environments,
    selected,
    revisions,
    exactRevision,
    canSelect,
    valid,
    loading: canRead && (!current || current.loading),
    error: current?.error || "",
    reload: () => setRetry((value) => value + 1),
  };
}
