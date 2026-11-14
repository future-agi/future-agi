import { useEffect, useState } from "react";
import { useLLMTracingStoreShallow } from "src/sections/projects/LLMTracing/states";

// A trace counts as open over a call only while the Traces panel that shows it is
// mounted. The open trace is URL-synced, so a reload or a shared link can bring it
// back with no panel on screen; it must not hold the call's arrows or Escape, and
// the stale key is cleared.
export default function useCallTraceOpen() {
  const [panelMounted, setPanelMounted] = useState(false);
  const { stored, setTraceDetailDrawerOpen } = useLLMTracingStoreShallow(
    (state) => ({
      stored: Boolean(state.traceDetailDrawerOpen),
      setTraceDetailDrawerOpen: state.setTraceDetailDrawerOpen,
    }),
  );

  useEffect(() => {
    if (stored && !panelMounted) setTraceDetailDrawerOpen(null);
  }, [stored, panelMounted, setTraceDetailDrawerOpen]);

  return {
    traceOpen: stored && panelMounted,
    onTracePanelMountedChange: setPanelMounted,
  };
}
