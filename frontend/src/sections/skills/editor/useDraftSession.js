import { useCallback, useEffect, useRef, useState } from "react";

import { getSkillErrorCode, useSaveSkillDraft } from "src/api/skills";

function sessionState(draft) {
  return { ...draft, savedFiles: draft.files, editSequence: 0, savedSequence: 0, pendingMutations: 0, mutationError: null, status: "saved", error: null };
}

function skillSaveInput(draft, revision) {
  return { files: draft.files, note: draft.note, baseVersion: draft.baseVersion, revision };
}

/** One owner for authored fields and server-owned dependency mutations. */
export default function useDraftSession(skillId, initialDraft, paused = false, resourceApi = null) {
  const [state, setState] = useState(() => sessionState(initialDraft));
  const latest = useRef(state);
  const inFlight = useRef(null);
  const flushing = useRef(null);
  const mutations = useRef(Promise.resolve());
  const generation = useRef(0);
  const mounted = useRef(true);
  const { mutateAsync } = useSaveSkillDraft(skillId);
  const saveDraft = resourceApi ? resourceApi.saveDraft : mutateAsync;
  const saveInput = resourceApi ? resourceApi.saveInput : skillSaveInput;
  const dirty = state.editSequence !== state.savedSequence || state.pendingMutations > 0 || Boolean(state.mutationError);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; };
  }, []);
  const update = useCallback((next) => {
    latest.current = next;
    if (mounted.current) setState(next);
  }, []);

  const save = useCallback(async (revision) => {
    if (!mounted.current) return null;
    if (inFlight.current) return inFlight.current;
    const snapshot = latest.current;
    if (snapshot.status === "conflict" && revision === undefined) return null;
    if (revision === undefined && snapshot.status === "saved" && snapshot.editSequence === snapshot.savedSequence && snapshot.revision > 0) return snapshot;
    const epoch = generation.current;
    update({ ...snapshot, status: "saving", error: null });
    const request = (async () => saveDraft(saveInput(snapshot, revision ?? snapshot.revision)))()
      .then((draft) => {
        if (!mounted.current || epoch !== generation.current) return null;
        update({ ...latest.current, revision: draft.revision, source: draft.source, savedFiles: snapshot.files, savedSequence: snapshot.editSequence, status: latest.current.mutationError ? "error" : "saved", error: latest.current.mutationError });
        return draft;
      }).catch((error) => {
        if (mounted.current && epoch === generation.current) update({ ...latest.current, status: getSkillErrorCode(error) === "draft_conflict" ? "conflict" : "error", error });
        return null;
      }).finally(() => { if (inFlight.current === request) inFlight.current = null; });
    inFlight.current = request;
    return request;
  }, [saveDraft, saveInput, update]);

  const flushLocal = useCallback(async () => {
    if (!mounted.current || latest.current.status === "conflict") return null;
    let acknowledged = inFlight.current ? await inFlight.current : await save(latest.current.revision);
    if (!acknowledged) return null;
    while (mounted.current && latest.current.editSequence !== latest.current.savedSequence) {
      if (latest.current.status === "conflict") return null;
      acknowledged = await save();
      if (!acknowledged) return null;
    }
    return mounted.current ? acknowledged : null;
  }, [save]);

  const flush = useCallback(async () => {
    if (flushing.current) return flushing.current;
    const request = (async () => {
      let tail;
      let acknowledged;
      do {
        tail = mutations.current;
        if (latest.current.pendingMutations) await tail;
        if (latest.current.mutationError) return null;
        acknowledged = await flushLocal();
        if (!acknowledged) return null;
      } while (tail !== mutations.current || latest.current.editSequence !== latest.current.savedSequence);
      return acknowledged;
    })().finally(() => { flushing.current = null; });
    flushing.current = request;
    return request;
  }, [flushLocal]);

  const mutateDraft = useCallback((operation) => {
    const epoch = generation.current;
    update({ ...latest.current, pendingMutations: latest.current.pendingMutations + 1, mutationError: null });
    const request = mutations.current.then(async () => {
      if (!mounted.current || epoch !== generation.current) return null;
      const acknowledged = await flushLocal();
      if (!mounted.current || epoch !== generation.current) return null;
      if (!acknowledged) {
        // A failed prerequisite save means the requested server mutation never
        // ran. A later ordinary save must not acknowledge that omitted intent.
        const error = latest.current.error ?? new Error("Save failed before the draft operation. Retry the operation or reload the draft.");
        update({ ...latest.current, mutationError: error });
        return null;
      }
      const snapshot = latest.current;
      update({ ...snapshot, status: "saving" });
      const mutation = Promise.resolve().then(() => operation(acknowledged)).then((draft) => {
        if (!mounted.current || epoch !== generation.current) return null;
        // Files/purpose typed during dependency Apply remain local and are saved
        // against its new revision; only the server can replace the accepted lock.
        const local = latest.current;
        const authored = local.editSequence === snapshot.editSequence ? {} : { files: local.files, note: local.note, description: local.description };
        update({ ...local, ...draft, ...authored, savedFiles: draft.files, savedSequence: snapshot.editSequence, status: "saved", error: null });
        return draft;
      }).catch((error) => {
        if (mounted.current && epoch === generation.current) update({ ...latest.current, mutationError: error, status: getSkillErrorCode(error) === "draft_conflict" ? "conflict" : "error", error });
        return null;
      }).finally(() => { if (inFlight.current === mutation) inFlight.current = null; });
      inFlight.current = mutation;
      return mutation;
    }).finally(() => {
      if (mounted.current && epoch === generation.current) update({ ...latest.current, pendingMutations: latest.current.pendingMutations - 1 });
    });
    mutations.current = request;
    return request;
  }, [flushLocal, update]);

  const editFields = useCallback((fields) => {
    const allowed = ["files", "note", "description"];
    if (Object.keys(fields).some((field) => !allowed.includes(field))) throw new Error("Use mutateDraft for server-owned draft state.");
    update({ ...latest.current, ...fields, editSequence: latest.current.editSequence + 1 });
  }, [update]);

  useEffect(() => {
    if (paused || !dirty || state.pendingMutations || ["saving", "error", "conflict"].includes(state.status)) return undefined;
    const timer = setTimeout(() => { save(); }, 1000);
    return () => clearTimeout(timer);
  }, [dirty, state.editSequence, state.pendingMutations, state.status, save, paused]);

  return {
    ...state, dirty, save, flush, editFields, mutateDraft,
    edit: (files) => editFields({ files }),
    reload: (draft) => {
      generation.current += 1;
      update(sessionState(draft));
    },
  };
}
