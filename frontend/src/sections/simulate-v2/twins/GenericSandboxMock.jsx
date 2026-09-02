import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button } from "@mui/material";
import Iconify from "src/components/iconify";
import TwinLogo from "../components/TwinLogo";

/**
 * Placeholder inline preview for a twinned service whose bespoke mock
 * isn't built yet. Reads the same shape as SlackSandboxMock — a large
 * card, the service brand at center, a "Open surface" prompt — so the
 * Twin detail page has a consistent visual weight regardless of which
 * service the twin backs. Real per-service mocks (Notion, Gmail,
 * Salesforce) get built out over time and displace this one.
 */
export default function GenericSandboxMock({ twin }) {
  return (
    <Box sx={{
      borderRadius: 1.5, overflow: "hidden",
      border: "1px solid", borderColor: "divider",
      bgcolor: "background.paper",
      height: 800, display: "flex", flexDirection: "column",
    }}>
      {/* Chrome header — mimics a browser address bar so it reads as
          "the sandbox is running in a window" rather than "empty state". */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{
          px: 1.5, py: 1, flexShrink: 0,
          bgcolor: "background.neutral",
          borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        <Stack direction="row" spacing={0.5}>
          <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: "#F87171" }} />
          <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: "#FBBF24" }} />
          <Box sx={{ width: 10, height: 10, borderRadius: "50%", bgcolor: "#34D399" }} />
        </Stack>
        <Box sx={{
          flex: 1, maxWidth: 480, height: 22, borderRadius: 0.75, px: 1,
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.paper",
          display: "flex", alignItems: "center", gap: 0.5,
        }}>
          <Iconify icon="solar:lock-keyhole-linear" width={10} sx={{ color: "text.subtitle" }} />
          <Typography sx={{
            fontSize: 11, color: "text.subtitle",
            fontFamily: "ui-monospace, Menlo, monospace",
          }} noWrap>
            {twin?.id || "service"}.sandbox.futureagi.com
          </Typography>
        </Box>
      </Stack>

      {/* Body — brand mark + "Open surface" prompt */}
      <Stack alignItems="center" justifyContent="center" spacing={2.5} sx={{ flex: 1, p: 4 }}>
        <Box sx={{
          width: 64, height: 64, borderRadius: 1.5,
          display: "grid", placeItems: "center",
          border: "1px solid", borderColor: "divider",
          bgcolor: "background.default",
        }}>
          <TwinLogo twin={twin} width={40} />
        </Box>
        <Box sx={{ textAlign: "center", maxWidth: 380 }}>
          <Typography sx={{ typography: "s1_2", fontWeight: 700 }}>
            {twin?.name || "Sandbox"} is serving
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.75 }}>
            Your agent&apos;s calls to {twin?.name || "this service"} land in this sandbox. Open the surface to inspect the state your agent leaves behind.
          </Typography>
        </Box>
        <Button
          variant="contained" color="primary" size="small"
          startIcon={<Iconify icon="solar:square-top-down-linear" width={13} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Open surface
        </Button>
      </Stack>

      {/* Footer — depth summary so viewers understand what's in the twin */}
      <Stack
        direction="row" alignItems="center" spacing={0.75} flexWrap="wrap" useFlexGap
        sx={{
          px: 1.75, py: 1.25, flexShrink: 0,
          borderTop: "1px solid", borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
          Modelled
        </Typography>
        {(twin?.depth || []).map((d) => (
          <Typography key={d} sx={{
            px: 0.75, py: 0.125, borderRadius: 0.5,
            typography: "s3", fontWeight: 600, color: "text.secondary",
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
          }}>
            {d}
          </Typography>
        ))}
      </Stack>
    </Box>
  );
}

GenericSandboxMock.propTypes = { twin: PropTypes.object };
