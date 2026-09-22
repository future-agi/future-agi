import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";

import SideDrawer from "../../../../components/SideDrawer";
import CreateEditOptimizationForm from "src/sections/test-detail/CreateEditOptimization/CreateEditOptimizationForm";

/**
 * Launch a self-improvement (optimization) run — REAL.
 *
 * A real launch endpoint exists (`optimizeSimulate.createOptimization`, POST
 * `/simulate/api/agent-prompt-optimiser/`) and the product already owns a
 * self-contained config form that posts the correct payload — it reads the
 * execution id from the route (`useParams().executionId`, which matches this
 * page's `runs/:testId/:executionId`) and calls `onSuccess`/`onClose`. So rather
 * than port the designer's mock `CreateOptimizationModal`, this reuses the real
 * product form inside the shared drawer.
 */
export default function LaunchOptimizationDrawer({ open, onClose, onLaunched }) {
  return (
    <SideDrawer open={open} onClose={onClose} width={{ xs: "100%", sm: 560 }}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Box sx={{ px: 2.5, py: 2, pr: 5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>Launch optimization</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Search for a better agent prompt against this run&apos;s failures.
          </Typography>
        </Box>
        <Box sx={{ flex: 1, overflowY: "auto", p: 2.5 }}>
          {open && (
            <CreateEditOptimizationForm
              onClose={onClose}
              // The product form's own mutation calls onClose after onSuccess, so
              // this just refreshes the runs list — no second close.
              onSuccess={() => onLaunched?.()}
            />
          )}
        </Box>
      </Stack>
    </SideDrawer>
  );
}

LaunchOptimizationDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onLaunched: PropTypes.func,
};
