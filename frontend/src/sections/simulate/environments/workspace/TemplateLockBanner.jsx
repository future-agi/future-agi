import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
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
        spacing={1.75}
        sx={{
          px: 2, py: 1.25, borderRadius: 1.5,
          border: "1px solid",
          borderColor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.32 : 0.22),
          background: (t) =>
            `linear-gradient(90deg, ${alpha("#7857FC", t.palette.mode === "dark" ? 0.18 : 0.09)} 0%, ${alpha("#7857FC", t.palette.mode === "dark" ? 0.06 : 0.03)} 45%, ${t.palette.background.paper} 100%)`,
          boxShadow: (t) => `inset 0 0 0 1px ${alpha("#7857FC", t.palette.mode === "dark" ? 0.06 : 0.04)}`,
        }}
      >
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1, flexShrink: 0,
            display: "grid", placeItems: "center",
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.22 : 0.14),
            border: (t) => `1px solid ${alpha("#7857FC", t.palette.mode === "dark" ? 0.35 : 0.24)}`,
          }}
        >
          <Iconify icon="solar:lock-keyhole-bold" width={16} sx={{ color: "#7857FC" }} />
        </Box>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary" }}>
              You&apos;re viewing a template
            </Typography>
            <Box
              sx={{
                px: 0.75, py: 0.125, borderRadius: 0.75,
                typography: "s3", fontWeight: 700, letterSpacing: 0.5,
                color: "#7857FC",
                bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.2 : 0.12),
              }}
            >
              TEMPLATE
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
          startIcon={<Iconify icon="solar:copy-linear" width={13} sx={{ color: "common.black" }} />}
          sx={{
            typography: "s2", fontWeight: 700,
            bgcolor: "common.white",
            color: "common.black",
            "&:hover": {
              bgcolor: (t) => alpha(t.palette.common.white, 0.9),
              color: "common.black",
            },
            flexShrink: 0,
          }}
        >
          Fork to edit
        </Button>
      </Stack>
    </Box>
  );
}

TemplateLockBanner.propTypes = { onFork: PropTypes.func };
