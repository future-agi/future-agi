import { useEffect, useRef, useState } from "react";

export const PRIVATE_AGENT_SHARING = { mode: "private", accountIds: [] };
function canonicalAccountId(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.trim() === value &&
    new TextEncoder().encode(value).length <= 256 &&
    !Array.from(value).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  );
}
export function readAgentSharing(value) {
  if (
    !value ||
    !["private", "workspace", "selected"].includes(value.mode) ||
    !Array.isArray(value.accountIds) ||
    value.accountIds.length > 64 ||
    value.accountIds.some((id) => !canonicalAccountId(id)) ||
    new Set(value.accountIds).size !== value.accountIds.length ||
    (value.mode !== "selected" && value.accountIds.length > 0) ||
    (value.mode === "selected" && value.accountIds.length === 0)
  )
    return null;
  return { mode: value.mode, accountIds: [...value.accountIds] };
}
export function agentSharingLabel(sharing) {
  if (sharing?.mode === "private") return "Private — only the agent owner";
  if (sharing?.mode === "workspace") return "Everyone in this workspace";
  if (sharing?.mode === "selected")
    return `Selected people (${sharing.accountIds.length})`;
  return "Sharing policy unavailable";
}

export function useAgentSharingDirectory({
  client,
  active,
  selectedAccountIds,
}) {
  const [state, setState] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const selectedRef = useRef(selectedAccountIds || []);
  selectedRef.current = selectedAccountIds || [];
  useEffect(() => {
    if (!active) {
      setState(null);
      setSearch("");
      setQuery("");
      return undefined;
    }
    let canceled = false;
    const identity = { client, attempt, query };
    const retained = (prior) =>
      prior?.client === client
        ? (prior.known || []).filter((entry) =>
            selectedRef.current.includes(entry.accountId),
          )
        : [];
    setState((prior) => ({
      ...identity,
      loading: true,
      known: retained(prior),
    }));
    Promise.resolve()
      .then(() => client.listWorkforceSharingCandidates({ query }))
      .then((result) => {
        if (canceled) return;
        const ids = new Set();
        const valid =
          result?.available === true &&
          result.schemaVersion === "omega-workforce/v1" &&
          typeof result.truncated === "boolean" &&
          Array.isArray(result.candidates) &&
          result.candidates.length <= 200 &&
          result.candidates.every((entry) => {
            if (
              !entry ||
              !canonicalAccountId(entry.accountId) ||
              typeof entry.displayName !== "string" ||
              !entry.displayName ||
              entry.displayName.length > 4000 ||
              ids.has(entry.accountId)
            )
              return false;
            ids.add(entry.accountId);
            return true;
          });
        setState((prior) => ({
          ...identity,
          loading: false,
          result: valid ? result : null,
          known: valid
            ? [
                ...new Map(
                  [
                    ...(query === "" && !result.truncated
                      ? []
                      : retained(prior)),
                    ...result.candidates,
                  ].map((entry) => [entry.accountId, entry]),
                ).values(),
              ]
            : [],
          error: valid
            ? ""
            : (typeof result?.reason === "string" && result.reason) ||
              "The workspace member list could not be verified.",
        }));
      })
      .catch(() => {
        if (!canceled)
          setState({
            ...identity,
            loading: false,
            error: "Couldn't read workspace members. Your draft is kept here.",
          });
      });
    return () => {
      canceled = true;
    };
  }, [client, active, attempt, query]);
  const current =
    active &&
    state?.client === client &&
    state.attempt === attempt &&
    state.query === query
      ? state
      : null;
  return {
    candidates: current?.result?.candidates || [],
    knownCandidates: current?.known || [],
    available: Boolean(current?.result),
    truncated: current?.result?.truncated === true,
    loading: active && (!current || current.loading),
    error: current?.error || "",
    reload: () => setAttempt((value) => value + 1),
    search,
    setSearch,
    searchMembers: () => {
      setQuery(search.trim());
      setAttempt((value) => value + 1);
    },
  };
}
