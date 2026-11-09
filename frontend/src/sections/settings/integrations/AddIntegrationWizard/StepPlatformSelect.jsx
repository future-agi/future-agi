import PropTypes from "prop-types";
import { Box, Card, CardActionArea, Typography, useTheme } from "@mui/material";
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
    <Box display="flex" flexDirection="column" gap={theme.spacing(2)}>
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
        <Card
          key={platform.id}
          variant="outlined"
          sx={{
            border: data.platform === platform.id ? 2 : 1,
            borderColor:
              data.platform === platform.id ? "primary.main" : "divider",
          }}
        >
          <CardActionArea
            onClick={() => handleSelect(platform.id)}
            sx={{
              p: theme.spacing(2),
              display: "flex",
              alignItems: "center",
              gap: theme.spacing(2),
            }}
          >
            <PlatformLogo platform={platform.id} size={40} />
            <Box flex={1}>
              <Typography
                sx={{
                  typography: "s1",
                  fontWeight: "fontWeightMedium",
                  color: "text.primary",
                }}
              >
                {platform.name}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.disabled" }}>
                {platform.wizardDescription || platform.description}
              </Typography>
            </Box>
          </CardActionArea>
        </Card>
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
