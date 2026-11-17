import { useEffect, useState } from "react";

/** The current time, refreshed every `intervalMs`, for "in 12 min" style labels. */
export default function useNow(intervalMs = 30_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
