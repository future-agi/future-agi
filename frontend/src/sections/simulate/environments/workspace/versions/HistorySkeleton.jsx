import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";
import { visuallyHidden } from "@mui/utils";
import MetricStrip from "src/components/metric-strip";
import { historyCard } from "./historyCard";

const bar = (width, height, sx) => (
  <Skeleton variant="rounded" width={width} height={height} sx={sx} />
);

// Read once by screen readers; the grey shapes stay silent.
function Busy({ label, children, sx }) {
  return (
    <Box aria-busy="true" sx={sx}>
      <Box role="status" sx={visuallyHidden}>
        {label}
      </Box>
      {children}
    </Box>
  );
}
Busy.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  sx: PropTypes.object,
};

// Sized to a timeline row: kind icon, title line, meta line, actions.
function EntryRowSkeleton() {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      alignItems="flex-start"
      sx={{ px: 2.5, py: 1.25, borderTop: "1px solid", borderColor: "divider" }}
    >
      {bar(28, 28, { flexShrink: 0, borderRadius: "6px" })}
      <Stack flex={1} minWidth={0} spacing={0.75} sx={{ pt: 0.5 }}>
        {bar("32%", 12)}
        {bar("55%", 10)}
      </Stack>
      {bar(20, 20, { borderRadius: "6px", mt: 0.5 })}
    </Stack>
  );
}

// The History tab while its versions load: tiles and timeline in the places
// they will land, so nothing jumps when they arrive.
export default function HistorySkeleton({ label }) {
  return (
    <Busy label={label}>
      <Stack spacing={2} sx={{ p: 3 }}>
        {/* As tall as the strip's own lines, so nothing jumps. */}
        <MetricStrip
          wrap
          items={[0, 1, 2, 3].map((tile) => ({
            key: String(tile),
            label: <Skeleton width="40%" height={16} />,
            value: <Skeleton width={48} height={28} />,
            hint: <Skeleton width="60%" height={18} />,
          }))}
        />
        <Box sx={historyCard}>
          <Stack spacing={1} sx={{ px: 2.5, py: 2 }}>
            {bar(80, 14)}
            {bar("85%", 10)}
            {bar(300, 30, { mt: 1, maxWidth: "100%" })}
          </Stack>
          {[0, 1, 2, 3].map((row) => (
            <EntryRowSkeleton key={row} />
          ))}
        </Box>
      </Stack>
    </Busy>
  );
}
HistorySkeleton.propTypes = { label: PropTypes.string.isRequired };

// A version's changes while they load: a group heading and a few names.
export function ChangesSkeleton({ label }) {
  return (
    <Busy label={label}>
      <Stack spacing={1}>
        {bar("22%", 12)}
        {bar("48%", 10)}
        {bar("40%", 10)}
        {bar("44%", 10)}
      </Stack>
    </Busy>
  );
}
ChangesSkeleton.propTypes = { label: PropTypes.string.isRequired };
