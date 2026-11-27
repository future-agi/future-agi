import { useEffect, useState } from "react";

/** Now, ticking every `ms` while `active` — re-reads a live log as it grows. */
export default function useLogClock(active, ms = 2000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [active, ms]);
  return now;
}
