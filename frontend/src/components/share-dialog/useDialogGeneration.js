import { useEffect, useMemo, useRef } from "react";

// Numbers each dialog session. The number moves on when the dialog closes or
// switches resource, so async work started earlier (clipboard, PATCH, reread)
// can tell it is stale and must not act on the next session.
export default function useDialogGeneration(open, contextKey) {
  const generation = useRef(0);
  const listeners = useRef(new Set());
  const prevContext = useRef(contextKey);
  const prevOpen = useRef(open);

  useEffect(() => {
    const contextChanged = prevContext.current !== contextKey;
    const closed = prevOpen.current && !open;
    prevContext.current = contextKey;
    prevOpen.current = open;
    if (!contextChanged && !closed) return;
    generation.current += 1;
    listeners.current.forEach((listener) => listener({ contextChanged }));
  }, [contextKey, open]);

  return useMemo(
    () => ({
      get current() {
        return generation.current;
      },
      subscribe(listener) {
        listeners.current.add(listener);
        return () => listeners.current.delete(listener);
      },
    }),
    [],
  );
}

// Runs `onReset` when a new session starts, in the same commit and after the
// generation has moved on. It always sees the values of the latest render.
export function useGenerationReset(generation, onReset) {
  const latest = useRef(onReset);
  latest.current = onReset;
  useEffect(
    () => generation.subscribe((info) => latest.current(info)),
    [generation],
  );
}
