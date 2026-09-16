import PropTypes from "prop-types";
import { Box } from "@mui/material";

import SectionCard from "../../components/SectionCard";
import BuilderConsole from "../console/BuilderConsole";
import PanelBoundary from "./PanelBoundary";
import BuildingPane from "./BuildingPane";

/**
 * The two-pane building stage.
 *
 * Chat narrow, artifacts wide — the shape Lovable, Figma Make and v0 all landed
 * on for the same reason: the input is a column of text and the output is a
 * whole surface. The console drives the build on the left; the right pane shows
 * the engine working, guarded by a component-scoped error boundary so a bad
 * derived shape can't take the chat down with it.
 *
 * `progress` is the `useBuildProgress` return; it may be null mid-init, so we
 * null-guard it and let the panes fall through to their own defaults.
 */
export default function BuildingStage({ progress }) {
  const p = progress || {};
  return (
    <Box
      sx={{
        display: "grid", gap: 2, height: "100%", minHeight: 0,
        gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 400px) 1fr" },
      }}
    >
      <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <BuilderConsole
          turns={p.turns}
          running={p.running}
          chips={p.chips}
          onSend={p.send}
          onChip={p.onChip}
        />
      </SectionCard>

      <SectionCard sx={{ minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 2.5 }}>
        <PanelBoundary>
          <BuildingPane done={p.done} running={p.running} failure={p.failure} />
        </PanelBoundary>
      </SectionCard>
    </Box>
  );
}

BuildingStage.propTypes = {
  progress: PropTypes.shape({
    done: PropTypes.arrayOf(PropTypes.string),
    running: PropTypes.bool,
    failure: PropTypes.shape({
      stepId: PropTypes.string,
      title: PropTypes.string,
      detail: PropTypes.string,
      retryable: PropTypes.bool,
    }),
    turns: PropTypes.array,
    chips: PropTypes.array,
    send: PropTypes.func,
    onChip: PropTypes.func,
  }),
};
