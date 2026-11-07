import { useCallback, useEffect, useRef, useState } from "react";

import { getSkillErrorCode, useSaveSkillDraft } from "src/api/skills";

/** The session owns local edits. A save acknowledges only its captured files. */
export default function useDraftSession(skillId, initialDraft, paused = false) {
  const [state, setState] = useState({ ...initialDraft, savedFiles: initialDraft.files, status: "saved", error: null });
  const latest = useRef(state);
  const inFlight = useRef(null);
  const flushing = useRef(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const { mutateAsync } = useSaveSkillDraft(skillId);
  const dirty = state.files !== state.savedFiles;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; };
  }, []);
  // Update the imperative snapshot synchronously, including save acknowledgments.
  // A flush must not wait for React's next render to see the new revision.
  const update = useCallback((next) => {
    latest.current = next;
    if (mounted.current) setState(next);
  }, []);

  const save = useCallback(async (revision) => {
    if (!mounted.current) return null;
    if (inFlight.current) return inFlight.current;
    const snapshot = latest.current;
    if (snapshot.status === "conflict" && revision === undefined) return null;
    if (revision === undefined && snapshot.status === "saved" && snapshot.files === snapshot.savedFiles && snapshot.revision > 0) return snapshot;
    const epoch = generation.current;
    update({ ...snapshot, status: "saving", error: null });
    const request = mutateAsync({ files: snapshot.files, note: snapshot.note, baseVersion: snapshot.baseVersion, revision: revision ?? snapshot.revision })
      .then((draft) => {
        if (!mounted.current || epoch !== generation.current) return null;
        update({ ...latest.current, revision: draft.revision, source: draft.source, savedFiles: snapshot.files, status: "saved", error: null });
        return draft;
      }).catch((error) => {
        if (mounted.current && epoch === generation.current) update({ ...latest.current, status: getSkillErrorCode(error) === "draft_conflict" ? "conflict" : "error", error });
        return null;
      }).finally(() => { inFlight.current = null; });
    inFlight.current = request;
    return request;
  }, [mutateAsync, update]);

  const flush = useCallback(async () => {
    if (flushing.current) return flushing.current;
    const request = (async () => {
      if (!mounted.current || latest.current.status === "conflict") return null;
      // Revalidate even an unchanged saved draft before review. Never reuse the
      // revision captured before an older in-flight request finishes.
      let acknowledged = inFlight.current ? await inFlight.current : await save(latest.current.revision);
      if (!acknowledged) return null;
      while (mounted.current && latest.current.files !== latest.current.savedFiles) {
        if (latest.current.status === "conflict") return null;
        acknowledged = await save();
        if (!acknowledged) return null;
      }
      return mounted.current ? acknowledged : null;
    })().finally(() => { flushing.current = null; });
    flushing.current = request;
    return request;
  }, [save]);

  useEffect(() => {
    if (paused || !dirty || ["saving", "error", "conflict"].includes(state.status)) return undefined;
    const timer = setTimeout(() => { save(); }, 1000);
    return () => clearTimeout(timer);
  }, [dirty, state.files, state.status, save, paused]);

  return {
    ...state, dirty, save, flush,
    edit: (files) => update({ ...latest.current, files }),
    reload: (draft) => {
      generation.current += 1;
      update({ ...draft, savedFiles: draft.files, status: "saved", error: null });
    },
  };
}
