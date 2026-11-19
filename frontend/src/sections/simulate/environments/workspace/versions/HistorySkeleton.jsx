import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";
import { visuallyHidden } from "@mui/utils";

const card = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
  minWidth: 0,
};
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

// The History tab while its versions load: tiles, timeline and side panel in
// the places they will land, so nothing jumps when they arrive.
export default function HistorySkeleton({ label }) {
  return (
    <Busy label={label}>
      <Stack spacing={2} sx={{ p: 3 }}>
        <Box
          sx={{
            ...card,
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "repeat(3, minmax(0, 1fr))" },
          }}
        >
          {[0, 1, 2].map((tile) => (
            <Stack key={tile} spacing={0.75} sx={{ px: 2, py: 1.5 }}>
              {bar("40%", 10)}
              {bar(48, 20)}
              {bar("60%", 10)}
            </Stack>
          ))}
        </Box>
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: {
              xs: "1fr",
              md: "minmax(0, 2fr) minmax(0, 1fr)",
            },
          }}
        >
          <Box sx={card}>
            <Stack spacing={1} sx={{ px: 2.5, py: 2 }}>
              {bar(80, 14)}
              {bar("85%", 10)}
              {bar(300, 30, { mt: 1, maxWidth: "100%" })}
            </Stack>
            {[0, 1, 2, 3].map((row) => (
              <EntryRowSkeleton key={row} />
            ))}
          </Box>
          <Box sx={{ ...card, alignSelf: "start" }}>
            <Box sx={{ px: 2, py: 1.5 }}>{bar(120, 14)}</Box>
            {[0, 1, 2, 3].map((row) => (
              <Stack
                key={row}
                direction="row"
                spacing={2}
                sx={{
                  px: 2,
                  py: 1.25,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                {bar(96, 12)}
                {bar(56, 12)}
              </Stack>
            ))}
          </Box>
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
