import PropTypes from "prop-types";
import { Box, Chip, Typography, useTheme } from "@mui/material";
import Section from "src/components/section";
import PlatformLogo from "../PlatformLogo";
import { PLATFORMS } from "../constants";

export default function StepPlatformSelect({ data, onUpdate, onNext }) {
  const theme = useTheme();

  const handleSelect = (platformId) => {
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

      {PLATFORMS.map((platform) => (
        <Section
          key={platform.id}
          variant="panel"
          disabled={!platform.available}
          onClick={() => handleSelect(platform.id)}
          sx={{
            opacity: platform.available ? 1 : 0.5,
            borderColor:
              data.platform === platform.id ? "text.primary" : "divider",
            display: "flex",
            alignItems: "center",
            gap: theme.spacing(1.5),
          }}
        >
          <PlatformLogo platform={platform.id} size={28} />
          <Box flex={1}>
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
              {!platform.available && (
                <Chip label="Coming Soon" size="small" variant="outlined" />
              )}
            </Box>
            <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
              {platform.wizardDescription || platform.description}
            </Typography>
          </Box>
        </Section>
      ))}
    </Box>
  );
}

StepPlatformSelect.propTypes = {
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
};
