import PropTypes from "prop-types";
import { useReducer, useRef, useState } from "react";
import { Box, Stack, Typography, Button, TextField, IconButton, Tooltip } from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BUILD_HEADER_COPY } from "./build.constants";
import { STEP_SHAPE } from "./PipelineRow";
import MilestonePill from "./MilestonePill";
import PipelinePopover from "./PipelinePopover";

// Edit-in-place name state: the derived name is corrected in the header rather
// than asked for in a form.
const nameReducer = (state, action) => {
  switch (action.type) {
    case "START":
      return { editing: true, draftName: action.name ?? "" };
    case "CHANGE":
      return { ...state, draftName: action.value };
    case "STOP":
      return { ...state, editing: false };
    default:
      return state;
  }
};

/*
  The build screen header. A back control (change source), the derived name
  corrected in place, the milestone pill in the centre (which anchors the full
  pipeline popover) and — only once setup is built — the Run-simulation button.
*/
export default function BuildHeader({
  onBack, name, onRename, pipeline, summary, setupDone, onRun, canRun, runBlockedReason,
}) {
  const [{ editing, draftName }, dispatch] = useReducer(nameReducer, { editing: false, draftName: name ?? "" });
  const [pipeAnchor, setPipeAnchor] = useState(null);
  // Enter and the follow-on blur both fire on commit; commit once.
  const committedRef = useRef(false);

  const commitName = () => {
    if (committedRef.current) return;
    committedRef.current = true;
    dispatch({ type: "STOP" });
    onRename?.(draftName);
  };

  const startEditing = () => {
    committedRef.current = false;
    dispatch({ type: "START", name });
  };

  return (
    <Stack
      direction="row" alignItems="center" spacing={2}
      sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
    >
      <CustomTooltip show size="small" title={BUILD_HEADER_COPY.back} arrow>
        <Button
          onClick={onBack}
          aria-label={BUILD_HEADER_COPY.back}
          sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle", flexShrink: 0 }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={18} />
        </Button>
      </CustomTooltip>

      {/* The derived name, corrected in place rather than asked for in a form. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0, minWidth: 0 }}>
        {editing ? (
          <TextField
            size="small" value={draftName} autoFocus
            onChange={(e) => dispatch({ type: "CHANGE", value: e.target.value })}
            onBlur={commitName}
            onKeyDown={(e) => e.key === "Enter" && commitName()}
            sx={{ width: 230, "& .MuiInputBase-input": { typography: "s1_2", fontWeight: "fontWeightBold", py: 0.5 } }}
          />
        ) : (
          <>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: "fontWeightBold", maxWidth: 230 }}>{name}</Typography>
            <Tooltip arrow title={BUILD_HEADER_COPY.rename}>
              <IconButton size="small" aria-label={BUILD_HEADER_COPY.rename} onClick={startEditing}>
                <Iconify icon="solar:pen-new-square-linear" width={14} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </Tooltip>
          </>
        )}
      </Stack>

      {/* Header fingerprint — the milestone pill, which anchors the pipeline popover. */}
      <Box sx={{ flex: 1, display: { xs: "none", md: "flex" }, justifyContent: "center" }}>
        <MilestonePill
          pipeline={pipeline}
          summary={summary}
          setupDone={setupDone}
          onClick={(e) => setPipeAnchor(e.currentTarget)}
        />
      </Box>

      <PipelinePopover
        anchor={pipeAnchor}
        onClose={() => setPipeAnchor(null)}
        pipeline={pipeline}
        summary={summary}
      />

      {/*
        Only once the environment is actually built. Before that — the read-audit,
        the derivation still streaming — there is nothing to run yet, so the button
        is absent rather than present-but-disabled.
      */}
      {setupDone && (
        <Tooltip arrow title={canRun ? "" : (runBlockedReason || "")}>
          <span>
            <Button
              variant="contained" color="primary"
              disabled={!canRun}
              onClick={onRun}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ flexShrink: 0, typography: "s2", fontWeight: "fontWeightBold" }}
            >
              {BUILD_HEADER_COPY.run}
            </Button>
          </span>
        </Tooltip>
      )}
    </Stack>
  );
}

BuildHeader.propTypes = {
  onBack: PropTypes.func,
  name: PropTypes.string,
  onRename: PropTypes.func,
  pipeline: PropTypes.arrayOf(STEP_SHAPE),
  summary: PropTypes.shape({
    done: PropTypes.number,
    total: PropTypes.number,
    running: PropTypes.bool,
    failed: STEP_SHAPE,
    label: PropTypes.string,
  }),
  setupDone: PropTypes.bool,
  onRun: PropTypes.func,
  canRun: PropTypes.bool,
  runBlockedReason: PropTypes.string,
};
