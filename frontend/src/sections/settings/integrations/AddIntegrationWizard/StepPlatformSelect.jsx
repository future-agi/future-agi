import PropTypes from "prop-types";
import { Box, Chip, Typography, useTheme } from "@mui/material";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import PlatformLogo from "../PlatformLogo";

export default function StepPlatformSelect({
  platforms,
  data,
  onUpdate,
  onNext,
  onSelectSlack,
}) {
  const theme = useTheme();

  const handleSelect = (platformId) => {
    if (platformId === "slack") {
      onSelectSlack();
      return;
    }
    onUpdate({ platform: platformId });
    onNext();
  };

  return (
    <Box display="flex" flexDirection="column" gap={theme.spacing(1.5)}>
      <Typography
        sx={{
          typography: "s1",
          color: "text.secondary",
          mb: theme.spacing(1),
        }}
      >
        Select the platform you want to connect
      </Typography>

      {platforms.map((platform) => (
        // Section draws its children in its own padded box, so the logo,
        // name and arrow sit in one row inside it, as on the page's cards.
        // The picked platform keeps the 1px border, in the primary colour.
        <Section
          key={platform.id}
          variant="panel"
          disabled={platform.available === false}
          onClick={() => handleSelect(platform.id)}
          sx={{
            opacity: platform.available === false ? 0.5 : 1,
            borderColor:
              data.platform === platform.id ? "primary.main" : "divider",
          }}
        >
          <Box display="flex" alignItems="center" gap={theme.spacing(1.5)}>
            <PlatformLogo platform={platform.id} size={28} />
            <Box flex={1} minWidth={0}>
              <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
                <Typography
                  sx={{
                    typography: "s2_1",
                    fontWeight: "fontWeightSemiBold",
                    color: "text.primary",
                  }}
                >
                  {platform.name}
                </Typography>
                {platform.available === false && (
                  <Chip label="Coming Soon" size="small" variant="outlined" />
                )}
              </Box>
              <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                {platform.wizardDescription || platform.description}
              </Typography>
            </Box>
            {platform.available !== false && (
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={16}
                height={16}
                sx={{ color: "text.disabled", flexShrink: 0 }}
              />
            )}
          </Box>
        </Section>
      ))}
    </Box>
  );
}

StepPlatformSelect.propTypes = {
  platforms: PropTypes.arrayOf(PropTypes.object).isRequired,
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onSelectSlack: PropTypes.func.isRequired,
};
