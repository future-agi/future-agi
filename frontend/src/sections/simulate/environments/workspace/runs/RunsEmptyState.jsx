import PropTypes from "prop-types";
import { Box, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import EmptyState from "../../components/EmptyState";
import { RUNS_EMPTY_COPY } from "../workspace.constants";

// The Runs tab before the first run: the first run is the baseline, so the
// tab offers to start it. Runs every scenario with the current repeats.
export default function RunsEmptyState({
  onStartRun,
  canRun,
  runBlockedReason,
  repeats = 1,
}) {
  return (
    <Box sx={{ p: 2 }}>
      <EmptyState
        icon="solar:play-circle-linear"
        title={RUNS_EMPTY_COPY.title}
        body={RUNS_EMPTY_COPY.body}
        action={
          <CustomTooltip
            show
            arrow
            size="small"
            title={
              canRun
                ? `Runs every scenario, ${repeats} ${repeats === 1 ? "time" : "times"} each`
                : runBlockedReason || ""
            }
          >
            <span>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={!canRun}
                onClick={() => onStartRun?.(undefined, repeats)}
                startIcon={<Iconify icon="solar:play-bold" width={14} />}
                sx={{ whiteSpace: "nowrap" }}
              >
                {RUNS_EMPTY_COPY.action}
              </Button>
            </span>
          </CustomTooltip>
        }
      />
    </Box>
  );
}
RunsEmptyState.propTypes = {
  onStartRun: PropTypes.func,
  canRun: PropTypes.bool,
  runBlockedReason: PropTypes.string,
  repeats: PropTypes.number,
};
