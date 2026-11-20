import PropTypes from "prop-types";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Template lock banner — a seeded-from-template env is read-only until forked.
 * Scenarios, evaluations, contract and agent all render locked; running the env
 * as-is stays enabled. Forking mints an editable copy and clears the lock.
 */
export default function TemplateLockBanner({ onFork }) {
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
          bgcolor: "background.neutral",
        }}
      >
        <Iconify
          icon="solar:lock-keyhole-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.25 }}>
            <Typography sx={{ typography: "s1", fontWeight: 500, color: "text.primary" }}>
              You&apos;re viewing a template
            </Typography>
            <Box
              sx={{
                px: 0.75,
                height: 18,
                display: "inline-flex",
                alignItems: "center",
                borderRadius: "4px",
                typography: "s3",
                fontWeight: 500,
                color: "text.secondary",
                bgcolor: "action.selected",
              }}
            >
              Template
            </Box>
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            Scenarios, evaluations, contract and agent are read-only. Run as-is, or fork to make changes.
          </Typography>
        </Box>

        <Button
          variant="contained"
          size="small"
          onClick={onFork}
          startIcon={<Iconify icon="solar:copy-linear" width={13} />}
          sx={{ flexShrink: 0 }}
        >
          Fork to edit
        </Button>
      </Stack>
    </Box>
  );
}

TemplateLockBanner.propTypes = { onFork: PropTypes.func };
