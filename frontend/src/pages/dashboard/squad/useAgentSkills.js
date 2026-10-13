import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const WORKSPACE_SKILLS_SCHEMA = "omega-workspace-skills/v1";
export const MAX_AGENT_SKILLS = 8;
const EMPTY = [];
const record = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const positive = (value) => Number.isSafeInteger(value) && value > 0;
const idValid = (value) =>
  typeof value === "string" && /^skill_[a-f0-9-]{36}$/.test(value);
const dateValid = (value) =>
  typeof value === "string" && Number.isFinite(Date.parse(value));
const reason = (value, fallback) =>
  typeof value?.reason === "string" && value.reason ? value.reason : fallback;
const revisionFields = [
  "id",
  "revision",
  "name",
  "description",
  "digest",
  "createdAt",
];
const metadataValid = (value) =>
  record(value) &&
  idValid(value.id) &&
  positive(value.revision) &&
  typeof value.name === "string" &&
  value.name.trim().length > 0 &&
  typeof value.description === "string" &&
  value.description.trim().length > 0 &&
  typeof value.digest === "string" &&
  /^sha256:[a-f0-9]{64}$/.test(value.digest) &&
  dateValid(value.createdAt);

export function validateSkillCatalog(result) {
  if (
    result?.available !== true ||
    result.schemaVersion !== WORKSPACE_SKILLS_SCHEMA ||
    !Array.isArray(result.skills) ||
    result.skills.length > 200 ||
    typeof result.canManage !== "boolean"
  ) {
    return {
      ok: false,
      error: reason(result, "The workspace skill catalog is unavailable."),
    };
  }
  const ids = new Set();
  for (const skill of result.skills) {
    if (
      !metadataValid(skill) ||
      ids.has(skill.id) ||
      !dateValid(skill.updatedAt) ||
      !["active", "archived"].includes(skill.status) ||
      Object.keys(skill).some(
        (key) => ![...revisionFields, "status", "updatedAt"].includes(key),
      )
    ) {
      return {
        ok: false,
        error: "The server returned a malformed workspace skill catalog.",
      };
    }
    ids.add(skill.id);
  }
  return {
    ok: true,
    skills: result.skills.map((entry) => ({ ...entry })),
    canManage: result.canManage,
  };
}

export function validateSkillRevisions(result, skill) {
  if (
    result?.available !== true ||
    result.schemaVersion !== WORKSPACE_SKILLS_SCHEMA ||
    result.skillId !== skill.id ||
    !Array.isArray(result.revisions) ||
    result.revisions.length !== skill.revision ||
    result.revisions.length > 50
  ) {
    return {
      ok: false,
      error: reason(
        result,
        "The skill revision list does not match the current catalog. Refresh skills.",
      ),
    };
  }
  for (const [index, revision] of result.revisions.entries()) {
    if (
      !metadataValid(revision) ||
      revision.id !== skill.id ||
      revision.revision !== index + 1 ||
      Object.keys(revision).some((key) => !revisionFields.includes(key))
    ) {
      return {
        ok: false,
        error: "The server returned malformed revisions for this skill.",
      };
    }
  }
  const latest = result.revisions.at(-1);
  if (!latest || revisionFields.some((key) => latest[key] !== skill[key])) {
    return {
      ok: false,
      error:
        "The skill changed while its revisions were loading. Refresh skills.",
    };
  }
  return {
    ok: true,
    revisions: result.revisions.map((entry) => ({ ...entry })),
  };
}

export function normalizeSkillRefs(value) {
  if (value === undefined)
    return { provided: false, refs: EMPTY, validShape: true };
  if (!Array.isArray(value) || value.length > MAX_AGENT_SKILLS)
    return { provided: true, refs: EMPTY, validShape: false };
  const seen = new Set();
  const refs = [];
  for (const ref of value) {
    if (
      !record(ref) ||
      Object.keys(ref).length !== 2 ||
      Object.keys(ref).some((key) => !["id", "revision"].includes(key)) ||
      !idValid(ref.id) ||
      !positive(ref.revision) ||
      seen.has(ref.id)
    ) {
      return { provided: true, refs: EMPTY, validShape: false };
    }
    seen.add(ref.id);
    refs.push({ id: ref.id, revision: ref.revision });
  }
  return { provided: true, refs, validShape: true };
}

export function useAgentSkills({
  client,
  open = true,
  allowed = false,
  refs,
} = {}) {
  const scope = useMemo(
    () => ({ client, open, allowed }),
    [client, open, allowed],
  );
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const generation = useRef(0);
  const catalogRef = useRef(null);
  const [state, setState] = useState(null);
  const visible = state?.scope === scope ? state : null;
  const refState = normalizeSkillRefs(refs);
  // The draft holds at most eight refs. A stable key avoids refetching merely
  // because the editor creates a fresh array for unchanged selections.
  const selectedKey = JSON.stringify(refState.refs.map((ref) => ref.id).sort());

  const refresh = useCallback(async () => {
    const request = ++generation.current;
    catalogRef.current = null;
    setState({
      scope,
      catalog: [],
      revisions: {},
      errors: {},
      loading: open && allowed,
      ready: false,
    });
    if (!open || !allowed) return;
    let result;
    try {
      result = await client?.listWorkspaceSkills?.();
    } catch {
      result = {
        available: false,
        reason:
          "The workspace skills could not be read. Refresh and try again.",
      };
    }
    if (currentScope.current !== scope || request !== generation.current)
      return;
    const checked = validateSkillCatalog(result);
    const snapshot = checked.ok
      ? { skills: checked.skills, generation: request }
      : null;
    catalogRef.current = snapshot;
    setState({
      scope,
      catalog: checked.ok ? checked.skills : [],
      revisions: {},
      loading: false,
      errors: checked.ok ? {} : { catalog: checked.error },
      ready: checked.ok,
      canManage: checked.ok && checked.canManage,
      snapshot,
    });
  }, [scope, open, allowed, client]);

  useEffect(() => {
    void refresh();
    return () => {
      generation.current += 1;
      catalogRef.current = null;
    };
  }, [refresh]);

  const snapshot = visible?.snapshot;
  useEffect(() => {
    if (!snapshot || !open || !allowed) return;
    const selectedIds = new Set(JSON.parse(selectedKey));
    const pending = snapshot.skills.filter(
      (skill) =>
        selectedIds.has(skill.id) &&
        skill.status === "active" &&
        !visible?.revisions?.[skill.id],
    );
    if (!pending.length) return;
    const current = () =>
      currentScope.current === scope &&
      catalogRef.current === snapshot &&
      generation.current === snapshot.generation;
    setState((old) => ({
      ...old,
      revisions: {
        ...old.revisions,
        ...Object.fromEntries(
          pending.map((skill) => [skill.id, { loading: true }]),
        ),
      },
    }));
    for (const skill of pending) {
      (async () => {
        let result;
        try {
          result = await client?.listWorkspaceSkillRevisions?.(skill.id);
        } catch {
          result = {
            available: false,
            reason:
              "The skill revisions could not be read. Refresh skills and try again.",
          };
        }
        if (!current()) return;
        const checked = validateSkillRevisions(result, skill);
        setState((old) => ({
          ...old,
          revisions: {
            ...old.revisions,
            [skill.id]: checked.ok
              ? { loading: false, entries: checked.revisions }
              : { loading: false, error: checked.error },
          },
        }));
      })();
    }
  }, [selectedKey, snapshot, visible?.revisions, scope, client, open, allowed]);

  const catalog = visible?.catalog || EMPTY;
  const revisionStates = visible?.revisions || {};
  const errors = { ...(visible?.errors || {}) };
  for (const ref of refState.refs)
    if (revisionStates[ref.id]?.error)
      errors[ref.id] = revisionStates[ref.id].error;
  const revisions = Object.fromEntries(
    Object.entries(revisionStates)
      .filter(([, entry]) => entry.entries)
      .map(([id, entry]) => [id, entry.entries]),
  );
  const unavailableRefs = refState.refs.filter((ref) => {
    const skill = catalog.find((entry) => entry.id === ref.id);
    return (
      !skill ||
      skill.status !== "active" ||
      !revisions[ref.id]?.some((entry) => entry.revision === ref.revision)
    );
  });
  const loading = Boolean(
    open &&
      allowed &&
      (!visible ||
        visible.loading ||
        refState.refs.some((ref) => revisionStates[ref.id]?.loading)),
  );
  const currentValid =
    refState.validShape &&
    (!refState.refs.length ||
      (open &&
        allowed &&
        visible?.ready === true &&
        !loading &&
        unavailableRefs.length === 0));
  return {
    catalog,
    revisions,
    loading,
    errors,
    currentValid,
    refsValid: currentValid,
    unavailableRefs,
    canManage: visible?.canManage === true,
    refresh,
    canSelect: open && allowed && visible?.ready === true && !visible?.loading,
    allowed,
    open,
  };
}
