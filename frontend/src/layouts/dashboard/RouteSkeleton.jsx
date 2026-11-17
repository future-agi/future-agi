import React, { useEffect, useState } from "react";
import {
  Box,
  CircularProgress,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import {
  SLOW_HINT_MS,
  VERY_SLOW_HINT_MS,
  alreadyWaitedMs,
  slowHintText,
} from "./slowHint";

// The first skeleton after a full load counts from the navigation (the
// wait began at reload, before boot); later ones from their own mount.
// L7c g1: once the person clicks or types, the next skeleton comes from an
// in-app navigation, so it counts from its own mount (not from the load).
let firstSinceLoad = true;
let interacted = false;
if (typeof window !== "undefined") {
  const mark = () => {
    interacted = true;
  };
  window.addEventListener("pointerdown", mark, { capture: true, once: true });
  window.addEventListener("keydown", mark, { capture: true, once: true });
}
const nowMs = () =>
  typeof performance !== "undefined" ? performance.now() : Date.now();

function useElapsedStep() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const already = alreadyWaitedMs({
      firstSinceLoad,
      interacted,
      sinceLoadMs: nowMs(),
    });
    firstSinceLoad = false;
    const at = (ms, value) =>
      setTimeout(() => setElapsed(value), Math.max(0, ms - already));
    const t1 = at(SLOW_HINT_MS, SLOW_HINT_MS);
    const t2 = at(VERY_SLOW_HINT_MS, VERY_SLOW_HINT_MS);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);
  return elapsed;
}

/**
 * RouteSkeleton — the dashboard route-level loading fallback (R3 G0 §B7):
 * the shell stays (nav + layout), the page shows a header skeleton and 8
 * rows sized like a table, never the full-screen rocket after boot. A
 * quiet `Still loading this page…` appears after 3 s (no layout shift: the
 * line's height is reserved).
 */
export default function RouteSkeleton() {
  const hint = slowHintText(useElapsedStep());
  return (
    <Box
      sx={{ px: 3, py: 2.5, width: 1 }}
      aria-busy="true"
      aria-label="Loading the page"
      data-testid="route-skeleton"
    >
      <Stack direction="row" alignItems="center" gap={1.5} sx={{ mb: 2.5 }}>
        <Skeleton variant="rounded" width={28} height={28} />
        <Skeleton variant="text" width={260} height={30} />
        <Box sx={{ flex: 1 }} />
        <Skeleton variant="rounded" width={120} height={32} />
      </Stack>
      <Stack direction="row" gap={1} sx={{ mb: 1 }}>
        {[88, 96, 72, 110].map((w) => (
          <Skeleton key={w} variant="rounded" width={w} height={28} />
        ))}
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        role="status"
        aria-live="polite"
        data-testid="route-skeleton-hint"
        sx={{ minHeight: 24, mb: 1, color: "text.secondary" }}
      >
        {hint && (
          <>
            <CircularProgress size={12} thickness={5} color="inherit" />
            <Typography typography="s2" color="text.secondary">
              {hint}
            </Typography>
          </>
        )}
      </Stack>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <Skeleton variant="rectangular" height={36} />
        {Array.from({ length: 8 }).map((_, i) => (
          <Stack
            key={i}
            direction="row"
            gap={2}
            alignItems="center"
            sx={{
              px: 2,
              height: 44,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Skeleton variant="text" width="22%" />
            <Skeleton variant="text" width="38%" />
            <Skeleton variant="text" width="14%" />
            <Skeleton variant="text" width="10%" />
          </Stack>
        ))}
      </Box>
    </Box>
  );
}
