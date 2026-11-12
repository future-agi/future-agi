import { useMemo, useRef } from "react";

/**
 * The refusals a form's typed entries still hold (KH-SCOPE §6 B1): a number
 * the rule refused, or a half-typed one, that the reviewer has not replaced
 * yet (useNumberDraft). The form passes `refFor(labelId)` as each
 * LabelInput's `entryRef` and hands `pending()` to labelProblems at submit,
 * so Submit and ⌘↵ refuse with the reason instead of saving the kept score.
 *
 * @returns {{refFor: (labelId: string) => Function,
 *   pending: () => Object<string, {label_id, name, kind, text}>}}
 */
export function useEntryRefusals() {
  const handles = useRef(new Map());
  const refs = useRef(new Map());
  return useMemo(
    () => ({
      // One stable callback ref per label, so inputs never re-attach.
      refFor: (labelId) => {
        const key = String(labelId);
        if (!refs.current.has(key)) {
          refs.current.set(key, (handle) => {
            if (handle) handles.current.set(key, handle);
            else handles.current.delete(key);
          });
        }
        return refs.current.get(key);
      },
      pending: () => {
        const out = {};
        handles.current.forEach((handle, key) => {
          const problem = handle?.refusal?.();
          if (problem) out[key] = problem;
        });
        return out;
      },
    }),
    [],
  );
}

export default useEntryRefusals;
