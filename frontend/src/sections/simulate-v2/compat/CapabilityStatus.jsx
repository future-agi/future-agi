import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";
import { COMPAT_STATUS } from "../_mock/compatibility";

/**
 * One capability's status, the same everywhere it appears — the agent's
 * Capabilities tab, the run dialog, a run's header. Untested and Stale keep
 * their own dots, so neither can pass for Supported.
 */
export default function CapabilityStatus({ status, checking = false }) {
  const meta = COMPAT_STATUS[status] || COMPAT_STATUS.untested;
  return (
    <Stack direction="row" alignItems="center" spacing={0.5}>
      <StatusDot
        status={checking ? "running" : meta.dot}
        size={6}
        live={checking}
        hollow={!checking && status === "untested"}
      />
      <Typography
        sx={{
          typography: "s2",
          color: checking ? "text.tertiary" : "text.secondary",
        }}
      >
        {checking ? "Checking…" : meta.label}
      </Typography>
    </Stack>
  );
}
CapabilityStatus.propTypes = {
  status: PropTypes.string.isRequired,
  /* The connection check is running and this row comes from it. */
  checking: PropTypes.bool,
};
