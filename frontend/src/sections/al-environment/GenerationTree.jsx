import PropTypes from "prop-types";
import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

/**
 * What a suite generation is doing, while it does it.
 *
 * Asking for fifty scenarios splits the work across the agent's use cases and runs several
 * writers at once. Without this the page shows one long silence and then fifty scenarios, which
 * is indistinguishable from a hang: the only question anybody has during those minutes is
 * whether it is still alive, and a spinner cannot answer it.
 *
 * So each use case is a row that says what it is doing and how many of its share it has proved.
 * A scenario only counts here once it has cleared all three gates, so the number climbing is
 * real progress rather than work attempted.
 */

const MARKS = {
  waiting: { glyph: "○", tone: "text.disabled" },
  running: { glyph: "◐", tone: "info.main" },
  done: { glyph: "●", tone: "success.main" },
  failed: { glyph: "✕", tone: "error.main" },
};

const Row = ({ slice }) => {
  const mark = MARKS[slice.state] || MARKS.waiting;
  const share = slice.wanted > 0 ? Math.min(100, (slice.kept / slice.wanted) * 100) : 0;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ fontFamily: ALK_MONO, fontSize: 12, minWidth: 0 }}
    >
      <Box component="span" sx={{ color: mark.tone, width: 12, flexShrink: 0 }}>
        {mark.glyph}
      </Box>
      <Box
        component="span"
        sx={{
          color: "text.secondary",
          fontVariantNumeric: "tabular-nums",
          width: 44,
          flexShrink: 0,
        }}
      >
        {`${slice.kept}/${slice.wanted}`}
      </Box>
      <Box
        component="span"
        title={slice.use_case}
        sx={{
          color: slice.state === "failed" ? "error.main" : "text.primary",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
          minWidth: 0,
        }}
      >
        {slice.use_case}
      </Box>
      <LinearProgress
        variant="determinate"
        value={share}
        sx={{ width: 56, height: 3, borderRadius: 2, flexShrink: 0 }}
      />
    </Stack>
  );
};

Row.propTypes = { slice: PropTypes.object.isRequired };

const GenerationTree = ({ generation }) => {
  const slices = generation?.slices || [];
  if (!slices.length) return null;

  const running = slices.filter((one) => one.state === "running").length;
  const done = slices.filter((one) => one.state === "done").length;
  const failed = slices.filter((one) => one.state === "failed").length;
  const settled = generation.state === "done";

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 1.25,
        display: "flex",
        flexDirection: "column",
        gap: 0.75,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ fontFamily: ALK_MONO, fontSize: 12, color: "text.secondary" }}
      >
        <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
          {settled ? "Suite generated" : "Writing the suite"}
        </Box>
        <Box component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>
          {`${generation.kept ?? 0}/${generation.asked ?? 0} scenarios`}
        </Box>
        <Box component="span">·</Box>
        <Box component="span">
          {settled
            ? `${done} of ${slices.length} use cases`
            : `${running} writing, ${done} done of ${slices.length}`}
        </Box>
        {failed > 0 && (
          <Box component="span" sx={{ color: "error.main" }}>
            {`· ${failed} failed`}
          </Box>
        )}
      </Stack>
      {slices.map((slice) => (
        <Row key={slice.use_case} slice={slice} />
      ))}
      {!settled && (
        <Typography
          sx={{ fontFamily: ALK_MONO, fontSize: 11, color: "text.disabled", mt: 0.25 }}
        >
          Each writer proves its own scenarios before they count, so the numbers move in steps.
        </Typography>
      )}
    </Box>
  );
};

GenerationTree.propTypes = {
  generation: PropTypes.shape({
    state: PropTypes.string,
    asked: PropTypes.number,
    kept: PropTypes.number,
    at_once: PropTypes.number,
    slices: PropTypes.array,
  }),
};

export default GenerationTree;
