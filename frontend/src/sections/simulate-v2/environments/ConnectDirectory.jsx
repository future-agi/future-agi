import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { LogoTile, QuietTag } from "./LandingBits";

/*
  What a paste was read as — shown under the search box of
  agents/connect/ConnectCatalog, which reads pastes and lists every way in.
*/

export function Detection({ found }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ minWidth: 0 }}
    >
      {found.warn ? (
        <Box
          sx={{
            width: 24,
            height: 24,
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            color: "error.main",
          }}
        >
          <Iconify icon={found.icon} width={20} />
        </Box>
      ) : (
        <LogoTile logo={found.logo} icon={found.icon} size={24} />
      )}
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}>
            {found.label}
          </Typography>
          {found.detail && (
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: "text.secondary",
                fontFamily:
                  found.kind === "describe"
                    ? undefined
                    : "ui-monospace, SFMono-Regular, Menlo, monospace",
                minWidth: 0,
              }}
            >
              {found.detail}
            </Typography>
          )}
          {found.tag && <QuietTag>{found.tag}</QuietTag>}
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {found.note}
        </Typography>
      </Box>
      {!found.blocked && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.disabled",
            flexShrink: 0,
            display: { xs: "none", md: "block" },
          }}
        >
          Press Enter
        </Typography>
      )}
    </Stack>
  );
}
Detection.propTypes = { found: PropTypes.object.isRequired };
