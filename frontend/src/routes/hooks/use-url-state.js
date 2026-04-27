import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

export function parseUrlValue(value, defaultValue) {
  if (value === null) return defaultValue;

  try {
    // Try to parse as JSON first
    return JSON.parse(value);
  } catch {
    // If parsing fails, return as is (for simple strings)
    return value;
  }
}

export function stringifyUrlValue(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function useUrlState(key, defaultValue) {
  const [searchParams, setSearchParams] = useSearchParams();

  // Keep useState for triggering re-renders
  const [value, setStateValue] = useState(() =>
    parseUrlValue(searchParams.get(key), defaultValue),
  );

  // Flag to track if the URL change was triggered internally
  const isInternalUpdate = useRef(false);

  // Update both state and URL.
  // Reads from `window.location.search` rather than the functional
  // setSearchParams form because react-router's prev arg in the functional
  // form doesn't reflect intermediate navigate() calls within the same
  // synchronous tick — when multiple useUrlState setters fire back-to-back
  // (e.g. setActiveTab → applyConfig → setCellHeight/etc), the later writes
  // clobber the earlier ones. window.location.search IS updated
  // synchronously by react-router's underlying history.replaceState, so
  // each setter merges with the latest URL state correctly.
  const setValue = useCallback(
    (newValue, options = { replace: true }) => {
      setStateValue((currentValue) => {
        const nextValue =
          typeof newValue === "function" ? newValue(currentValue) : newValue;

        isInternalUpdate.current = true;

        const newSearchParams = new URLSearchParams(window.location.search);
        newSearchParams.set(key, stringifyUrlValue(nextValue));
        setSearchParams(newSearchParams, { replace: options.replace });

        return nextValue;
      });
    },
    [key, setSearchParams],
  );

  const removeValue = useCallback(
    (options = { replace: true }) => {
      isInternalUpdate.current = true;

      const newSearchParams = new URLSearchParams(window.location.search);
      newSearchParams.delete(key);
      setSearchParams(newSearchParams, { replace: options.replace });

      setStateValue(defaultValue);
    },
    [key, setSearchParams, defaultValue],
  );

  // Handle external URL changes (like browser back/forward)
  useEffect(() => {
    if (isInternalUpdate.current) {
      isInternalUpdate.current = false;
      return;
    }

    const urlValue = searchParams.get(key) || stringifyUrlValue(defaultValue);
    const currentValue = stringifyUrlValue(value);

    if (currentValue === urlValue) {
      return;
    }

    const newValue = parseUrlValue(searchParams.get(key), defaultValue);
    setStateValue(newValue);
    // Dep array intentionally excludes `value`: the effect should only fire on
    // external URL changes. Including `value` causes spurious reruns on every
    // state update, where the stringified comparison can register a false
    // mismatch (e.g., when state carries a random id not in the URL payload)
    // and reset the state back to the URL's default.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, key, defaultValue]);

  return [value, setValue, removeValue];
}
