import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import GenerateDiffText from "src/sections/common/GenerateDiffText";
import JsonDiff from "src/sections/common/Values/JsonDiffView";
import { isJsonPair } from "src/sections/common/Values/jsonDiff";
import { diffLines } from "./diffSegments";

/**
 * The toolbar's `Missing Text` / `New Text` legend
 * (ExperimentBarDataRightSection), for places that show a TextDiff.
 */
export const TextDiffLegend = () => (
  <Stack direction="row" alignItems="center" gap={1.5}>
    <Stack direction="row" alignItems="center" gap={0.5}>
      <SvgColor
        src="/icons/datasets/diff_pencil.svg"
        sx={{ height: "16px", width: "16px", color: "red.500" }}
      />
      <Typography
        variant="s3"
        color="text.primary"
        fontWeight="fontWeightRegular"
      >
        Missing Text
      </Typography>
    </Stack>
    <Stack direction="row" alignItems="center" gap={0.5}>
      <SvgColor
        src="/icons/datasets/diff_pencil.svg"
        sx={{ height: "16px", width: "16px", color: "green.500" }}
      />
      <Typography
        variant="s3"
        color="text.primary"
        fontWeight="fontWeightRegular"
      >
        New Text
      </Typography>
    </Stack>
  </Stack>
);

/**
 * TextDiff — word or JSON diff of two values, rendered through the grid's
 * existing GenerateDiffText (the `--diff-*` colours). One diff renderer
 * in the app. R2 (L6): when both sides are JSON objects / lists (or JSON
 * text) it renders JsonDiff (by path, ignore paths, `Text` = this diff).
 *
 * Props (frozen):
 * @param {*} props.from  old value (removed text in red)
 * @param {*} props.to    new value (added text in green)
 * @param {"words"|"json"} [props.kind="words"]
 */
const TextDiff = ({ from, to, kind = "words" }) => {
  // R2 (P6, L6): two JSON objects / lists (or JSON text of them) diff by
  // path; `Text` in its header shows this line diff.
  const json = useMemo(() => isJsonPair(from, to), [from, to]);
  const lines = useMemo(
    () => (json ? [] : diffLines(from, to, kind)),
    [from, to, kind, json],
  );
  if (json) return <JsonDiff from={from} to={to} textKind={kind} />;
  return (
    <Box
      sx={{
        "& p": { m: 0 },
        ...(kind === "json" && {
          "& p, & span": {
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          },
        }),
      }}
    >
      {lines.map((segments, i) =>
        segments.length ? (
          <GenerateDiffText key={i} cellText={segments} />
        ) : (
          <Box key={i} sx={{ height: 18 }} />
        ),
      )}
    </Box>
  );
};

TextDiff.propTypes = {
  from: PropTypes.any,
  to: PropTypes.any,
  kind: PropTypes.oneOf(["words", "json"]),
};

export default TextDiff;
