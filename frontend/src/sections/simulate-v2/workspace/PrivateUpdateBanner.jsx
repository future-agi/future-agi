import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";

/*
  A private environment's update (clarified plan): the environment this was
  copied from saved a newer version. Review shows what changed there; taking
  it moves this copy's pin, skipping keeps it where it is. Nothing applies on
  its own.
*/
export default function PrivateUpdateBanner({ update, onTake, onSkip }) {
  const [open, setOpen] = useState(false);
  if (!update) return null;
  return (
    <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2,
          py: 1.25,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Iconify
          icon="solar:refresh-circle-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`Update available · ${update.sourceName} ${update.to}`}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Private · your workspace. This copy is pinned to ${update.from}; nothing changes until you review it.`}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setOpen(true)}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Review
        </Button>
      </Stack>
      {open && (
        <ConfirmDialog
          open
          onClose={() => setOpen(false)}
          PaperProps={{ sx: { maxWidth: 520 } }}
          title={`${update.sourceName}: ${update.from} → ${update.to}`}
          content={
            <Stack spacing={1}>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                What changed in the source since this copy was made. Your own
                edits here are kept; results from before and after the update
                are kept apart by version.
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: 2.5, typography: "s2" }}>
                {update.notes.map((n) => (
                  <li key={n.label}>{`${n.label} — ${n.note}`}</li>
                ))}
              </Box>
            </Stack>
          }
          action={
            <Stack direction="row" spacing={1}>
              <Button
                size="small"
                onClick={() => {
                  setOpen(false);
                  onSkip();
                }}
                sx={{ color: "text.secondary" }}
              >
                {`Skip ${update.to}`}
              </Button>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => {
                  setOpen(false);
                  onTake();
                }}
              >
                {`Take ${update.to}`}
              </Button>
            </Stack>
          }
        />
      )}
    </Box>
  );
}
PrivateUpdateBanner.propTypes = {
  update: PropTypes.object,
  onTake: PropTypes.func.isRequired,
  onSkip: PropTypes.func.isRequired,
};
