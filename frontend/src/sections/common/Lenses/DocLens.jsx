// DocLens (`kind="document"` / `"json"`): the input document on
// the left (the reused PDF viewer through MediaValue `size="card"`), the
// JSON outputs field by field on the right (JsonFieldsDiff), the answer
// first: `gemini-2.5-flash got 3 of 4 fields · warranty_months 24
// (expected 12)`. Below 1100 px of lens width the panes stack.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";
import ErrorState from "src/sections/common/ux/ErrorState";
import MediaValue from "src/sections/common/Media/MediaValue";
import { mediaRefOf } from "src/sections/common/Media/mediaRef";
import JsonFieldsDiff from "./JsonFieldsDiff";
import { docSummary, fieldRowsOf, laneName } from "./docModel";

const SIDE_BY_SIDE = 1100;

/** The input document: the value itself, or the first document among inputs. */
function documentOf(input) {
  if (input == null) return null;
  const direct = mediaRefOf(input);
  const one = Array.isArray(direct) ? direct[0] : direct;
  if (one?.kind === "document") return { name: null, value: input };
  if (typeof input === "object" && !Array.isArray(input))
    for (const [name, v] of Object.entries(input)) {
      const r = mediaRefOf(v, { columnType: undefined });
      const first = Array.isArray(r) ? r[0] : r;
      if (first?.kind === "document") return { name, value: v };
    }
  return null;
}

/** Without expected fields: the primary eval's own verdict, as data. */
function evalLine(lanes) {
  const parts = lanes
    .map((l) => {
      const s = Object.values(l?.result?.scores ?? {}).find(
        (x) => x && (x.display || x.reason),
      );
      if (!s || s.value == null || s.value >= 0.5) return null;
      return [laneName(l), s.display, s.reason].filter(Boolean).join(" · ");
    })
    .filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/** Width of an element through a callback ref (the lens root mounts after
 * its loading state). */
function useWidth() {
  const [el, setEl] = useState(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((e) => setW(e[0]?.contentRect?.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}

export default function DocLens({
  kind = "document",
  lanes = [],
  input,
  expected,
  height,
  loading,
  error,
  onRetry,
}) {
  const [ref, width] = useWidth();
  const wide = width >= SIDE_BY_SIDE;
  const doc = useMemo(
    () => (kind === "document" ? documentOf(input) : null),
    [kind, input],
  );
  const table = useMemo(
    () => fieldRowsOf({ lanes, expected }),
    [lanes, expected],
  );
  const summary = useMemo(
    () => docSummary(table, lanes) ?? evalLine(lanes),
    [table, lanes],
  );
  const parsedAll = table.parsed.every(Boolean);

  if (error)
    return (
      <Box sx={{ p: 3 }}>
        <ErrorState thing="these outputs" onRetry={onRetry} />
      </Box>
    );
  if (loading)
    return (
      <Stack gap={1} sx={{ p: 1.5, height }}>
        <Skeleton variant="text" width="60%" />
        <Skeleton variant="rounded" height="100%" />
      </Stack>
    );

  const fields = table.rows.length ? (
    <JsonFieldsDiff table={table} lanes={lanes} />
  ) : (
    <Typography typography="s2" color="text.secondary" sx={{ py: 2 }}>
      These outputs are not JSON objects, so there is no field table.
    </Typography>
  );

  return (
    <Stack
      ref={ref}
      tabIndex={0}
      data-modality-lens={kind}
      aria-label="Document lens: the input document and each run's fields"
      sx={{
        height: height ?? "100%",
        minHeight: 0,
        p: 1.5,
        gap: 1,
        outline: "none",
        "&:focus-visible": {
          boxShadow: (t) => `inset 0 0 0 2px ${t.palette.primary.main}`,
        },
      }}
    >
      {summary ? (
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          data-doc-summary=""
          sx={{ flexShrink: 0 }}
        >
          {summary}
        </Typography>
      ) : null}
      {!parsedAll && table.rows.length ? (
        <Typography typography="s3" color="warning.main" sx={{ flexShrink: 0 }}>
          {`${lanes
            .filter((_, i) => !table.parsed[i])
            .map(laneName)
            .join(", ")} did not return a JSON object; its cells read Missing.`}
        </Typography>
      ) : null}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns:
            doc && wide ? "minmax(0, 1fr) minmax(0, 1.25fr)" : "minmax(0, 1fr)",
          gap: 1.5,
          overflow: "auto",
          alignContent: "start",
        }}
      >
        {doc ? (
          <Stack gap={0.5} sx={{ minWidth: 0 }} data-doc-input="">
            <Typography typography="s3" color="text.secondary">
              {doc.name ? `Input · ${doc.name}` : "Input document"}
            </Typography>
            <MediaValue
              value={doc.value}
              size="card"
              maxHeight={wide ? 520 : 320}
            />
          </Stack>
        ) : null}
        <Box sx={{ minWidth: 0 }}>{fields}</Box>
      </Box>
    </Stack>
  );
}

DocLens.propTypes = {
  kind: PropTypes.oneOf(["document", "json"]),
  lanes: PropTypes.array,
  input: PropTypes.any,
  expected: PropTypes.any,
  height: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  loading: PropTypes.bool,
  error: PropTypes.bool,
  onRetry: PropTypes.func,
};
