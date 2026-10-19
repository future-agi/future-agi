import { useNavigate } from "react-router-dom";
import { Box, Stack, Typography, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { BROWSE_COPY } from "./prebuiltEnvironments.constants";
import ReleaseGallery from "src/pages/dashboard/harness/ReleaseGallery";

/**
 * Prebuilt environments: only released environments, each sealed with a proved suite, so every
 * "use" here goes through adaptation and becomes a release-backed composition.
 */
export default function PrebuiltEnvironmentsBrowse() {
  const navigate = useNavigate();

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.5}
        sx={{
          px: 2,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <CustomTooltip show arrow size="small" title={BROWSE_COPY.backTooltip}>
          <IconButton
            aria-label={BROWSE_COPY.backTooltip}
            size="small"
            onClick={() => navigate(paths.dashboard.simulate.environments.root)}
            sx={{ mt: 0.25 }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </IconButton>
        </CustomTooltip>
        <Box>
          <Typography
            sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}
          >
            {BROWSE_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {BROWSE_COPY.subtitle}
          </Typography>
        </Box>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <ReleaseGallery
          empty={
            <Typography
              sx={{
                typography: "s2",
                color: "text.subtitle",
                py: 6,
                textAlign: "center",
              }}
            >
              {BROWSE_COPY.emptyLibrary}
            </Typography>
          }
        />
      </Box>
    </Box>
  );
}
