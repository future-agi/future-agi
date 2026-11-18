import { useCallback, useRef, useState } from "react";

// Keep save events until their rows arrive. Names alone cannot distinguish a
// later edit from an earlier save that the user has since manually unchecked.
export default function usePendingEvalSelections() {
  const [autoSelectRequests, setAutoSelectRequests] = useState([]);
  const nextToken = useRef(0);
  const requestAutoSelect = useCallback((name) => {
    if (!name) return;
    const request = { name, token: ++nextToken.current };
    setAutoSelectRequests((pending) => [...pending, request]);
  }, []);
  const acknowledgeAutoSelect = useCallback((tokens) => {
    const applied = new Set(tokens);
    setAutoSelectRequests((pending) =>
      pending.filter(({ token }) => !applied.has(token)),
    );
  }, []);

  return { autoSelectRequests, requestAutoSelect, acknowledgeAutoSelect };
}
