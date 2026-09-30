import { useCallback, useEffect, useRef, useState } from "react";

// Mock connect handshake for the MCP reach. Nothing actually connects — the hook
// flips a "waiting" flag, then resolves after a short delay so the panel can show
// the listening state before it reports the client is through. Replace with the
// real handshake when the MCP bridge lands.
export function useMcpConnect(onConnected) {
  const [testing, setTesting] = useState(false);
  const timer = useRef();

  const connect = useCallback(() => {
    setTesting(true);
    timer.current = setTimeout(() => {
      setTesting(false);
      onConnected?.();
    }, 900);
  }, [onConnected]);

  useEffect(() => () => clearTimeout(timer.current), []);

  return { testing, connect };
}
