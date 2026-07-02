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
  const valueRef = useRef(value);

  // Flag to track if the URL change was triggered internally
  const isInternalUpdate = useRef(false);

  // Keep the latest setSearchParams / defaultValue in refs so setValue and
  // removeValue below can stay referentially stable across renders.
  // react-router recreates setSearchParams whenever the URL (searchParams)
  // changes; if setValue/removeValue depended on it directly, their identity
  // would churn on every URL write. Consumers that list these setters in an
  // effect dependency array (e.g. TraceDetailDrawerChild's setAnalysisExists
  // effect) would then re-run on every write, calling the setter again and
  // looping until React throws "Maximum update depth exceeded".
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;
  const defaultValueRef = useRef(defaultValue);
  defaultValueRef.current = defaultValue;

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
      const nextValue =
        typeof newValue === "function" ? newValue(valueRef.current) : newValue;

      valueRef.current = nextValue;
      setStateValue(nextValue);
      isInternalUpdate.current = true;

      const newSearchParams = new URLSearchParams(window.location.search);
      newSearchParams.set(key, stringifyUrlValue(nextValue));
      setSearchParamsRef.current(newSearchParams, { replace: options.replace });
    },
    [key],
  );

  const removeValue = useCallback(
    (options = { replace: true }) => {
      isInternalUpdate.current = true;

      const newSearchParams = new URLSearchParams(window.location.search);
      newSearchParams.delete(key);
      setSearchParamsRef.current(newSearchParams, { replace: options.replace });

      valueRef.current = defaultValueRef.current;
      setStateValue(defaultValueRef.current);
    },
    [key],
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
    valueRef.current = newValue;
    setStateValue(newValue);
  }, [searchParams, key, defaultValue, value]);

  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  return [value, setValue, removeValue];
}
