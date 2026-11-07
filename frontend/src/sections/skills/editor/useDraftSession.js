import { useCallback, useEffect, useReducer, useRef } from "react";

import { getSkillErrorCode, useSaveSkillDraft } from "src/api/skills";

function reducer(state, action) {
  switch (action.type) {
    case "edit":
      return { ...state, files: action.files };
    case "saving":
      return { ...state, status: "saving", error: null };
    case "saved":
      return {
        ...state,
        revision: action.draft.revision,
        savedFiles: action.files,
        status: "saved",
      };
    case "failed":
      return { ...state, status: action.conflict ? "conflict" : "error", error: action.error };
    case "reload":
      return { ...action.draft, savedFiles: action.draft.files, status: "saved", error: null };
    default:
      return state;
  }
}

/** Keep saves serialized and retain edits made while a request is in flight. */
export default function useDraftSession(skillId, initialDraft, paused = false) {
  const [state, dispatch] = useReducer(reducer, {
    ...initialDraft,
    savedFiles: initialDraft.files,
    status: "saved",
    error: null,
  });
  const latest = useRef(state);
  latest.current = state;
  const inFlight = useRef(null);
  const { mutateAsync } = useSaveSkillDraft(skillId);
  const dirty = state.files !== state.savedFiles;

  const save = useCallback(async (revision) => {
    if (inFlight.current) return inFlight.current;
    const snapshot = latest.current;
    if (revision === undefined && snapshot.status === "saved" && snapshot.files === snapshot.savedFiles && snapshot.revision > 0) {
      return snapshot;
    }
    dispatch({ type: "saving" });
    const request = mutateAsync({
      files: snapshot.files,
      note: snapshot.note,
      baseVersion: snapshot.baseVersion,
      revision: revision === undefined ? snapshot.revision : revision,
    }).then((draft) => {
      dispatch({ type: "saved", draft, files: snapshot.files });
      return draft;
    }).catch((error) => {
      dispatch({ type: "failed", error, conflict: getSkillErrorCode(error) === "draft_conflict" });
      return null;
    }).finally(() => { inFlight.current = null; });
    inFlight.current = request;
    return request;
  }, [mutateAsync]);

  useEffect(() => {
    if (paused || !dirty || state.status === "saving" || state.status === "error" || state.status === "conflict") {
      return undefined;
    }
    const timer = setTimeout(() => { save(); }, 1000);
    return () => clearTimeout(timer);
  }, [dirty, state.files, state.status, save, paused]);

  return {
    ...state,
    dirty,
    save,
    edit: (files) => dispatch({ type: "edit", files }),
    reload: (draft) => dispatch({ type: "reload", draft }),
  };
}
