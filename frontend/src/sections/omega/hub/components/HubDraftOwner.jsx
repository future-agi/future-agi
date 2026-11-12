/* eslint-disable react-refresh/only-export-components */
import React, {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";

const DraftContext = createContext(null);

/**
 * Page-lifetime memory, ABOVE SessionGate. This owns data, never mounted UI or
 * effects: the gate still unmounts every request-capable child while checking.
 * A slot has exactly one mounted consumer. Nothing is written to browser storage.
 */
export default function HubDraftOwner({ children }) {
  const [owner] = useState(() => ({ values: new Map(), refs: new Map() }));
  return (
    <DraftContext.Provider value={owner}>{children}</DraftContext.Provider>
  );
}
HubDraftOwner.propTypes = { children: PropTypes.node };

/** useState semantics, with the value retained across a session fallback. */
export function useHubDraftState(name, initial) {
  const owner = useContext(DraftContext);
  const [value, setValue] = useState(() => {
    if (owner?.values.has(name)) return owner.values.get(name);
    const first = typeof initial === "function" ? initial() : initial;
    owner?.values.set(name, first);
    return first;
  });
  const mounted = useRef(true);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const set = useCallback(
    (next) => {
      // An upload/mutation finishing after SessionGate closed must not update a
      // paused draft, or a new draft created by an explicit discard.
      if (!mounted.current) return;
      if (!owner) {
        setValue(next);
        return;
      }
      const resolved =
        typeof next === "function" ? next(owner.values.get(name)) : next;
      owner.values.set(name, resolved);
      setValue(resolved);
    },
    [name, owner],
  );
  return [value, set];
}

/** Retain the gate's workspace lock and the wizard's request fingerprint/key. */
export function useHubDraftRef(name, initial) {
  const owner = useContext(DraftContext);
  const local = useRef(initial);
  if (!owner) return local;
  if (!owner.refs.has(name)) owner.refs.set(name, local);
  return owner.refs.get(name);
}

/** Only explicit source replacement clears wizard slots; never a session probe. */
export function useDiscardHubWizard() {
  const owner = useContext(DraftContext);
  return useCallback(() => {
    if (!owner) return;
    for (const slots of [owner.values, owner.refs]) {
      for (const name of slots.keys()) {
        if (name.startsWith("wizard.")) slots.delete(name);
      }
    }
  }, [owner]);
}
