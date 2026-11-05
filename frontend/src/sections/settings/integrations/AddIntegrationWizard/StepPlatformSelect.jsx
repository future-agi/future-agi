import PropTypes from "prop-types";
import {
  Box,
  Card,
  CardActionArea,
  Chip,
  Typography,
  useTheme,
} from "@mui/material";
import PlatformLogo from "../PlatformLogo";
import { PLATFORMS } from "../constants";

export default function StepPlatformSelect({ data, onUpdate, onNext, onSelectSlack }) {
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

      {PLATFORMS.map((platform) => (
        <Card
          key={platform.id}
          variant="outlined"
          sx={{
            opacity: platform.available ? 1 : 0.5,
            border: data.platform === platform.id ? 2 : 1,
            borderColor:
              data.platform === platform.id ? "primary.main" : "divider",
          }}
        >
          <CardActionArea
            disabled={!platform.available}
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
              <Box display="flex" alignItems="center" gap={theme.spacing(1)}>
                <Typography
                  sx={{
                    typography: "s1",
                    fontWeight: "fontWeightMedium",
                    color: "text.primary",
                  }}
                >
                  {platform.name}
                </Typography>
                {!platform.available && (
                  <Chip label="Coming Soon" size="small" variant="outlined" />
                )}
              </Box>
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
  data: PropTypes.object.isRequired,
  onUpdate: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  onSelectSlack: PropTypes.func.isRequired,
};
