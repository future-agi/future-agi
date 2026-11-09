import {
  Box,
  Checkbox,
  Divider,
  FormControlLabel,
  Popover,
  Radio,
  RadioGroup,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";

const DisplayMenu = ({
  anchorEl,
  open,
  onClose,
  viewAllPrompts,
  onViewAllPromptsChange,
  showAllColumns,
  onShowAllColumnsChange,
  rowHeightMapping,
  currentRowHeight,
  onRowHeightChange,
  hasAgentConfigs,
}) => {
  const theme = useTheme();

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchorEl}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
      slotProps={{
        paper: {
          sx: {
            padding: theme.spacing(2),
            borderRadius: theme.spacing(1),
            minWidth: "216px",
          },
        },
      }}
    >
      <Box>
        <Box>
          <Typography
            typography="s3"
            sx={{
              fontWeight: "fontWeightRegular",
              textTransform: "uppercase",
              color: "text.disabled",
              letterSpacing: "0.5px",
              mb: 0.5,
            }}
          >
            Column Displays
          </Typography>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
            {hasAgentConfigs && (
              <FormControlLabel
                control={
                  <Checkbox
                    size="small"
                    checked={viewAllPrompts}
                    onChange={(e) => onViewAllPromptsChange(e.target.checked)}
                  />
                }
                label={
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightRegular"
                    sx={{ ml: 0.5 }}
                  >
                    View all prompts
                  </Typography>
                }
                sx={{ my: -0.75 }}
              />
            )}
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={showAllColumns}
                  onChange={(e) => onShowAllColumnsChange(e.target.checked)}
                />
              }
              label={
                <Typography
                  typography="s1"
                  fontWeight="fontWeightRegular"
                  sx={{ ml: 0.5 }}
                >
                  Show all columns
                </Typography>
              }
              sx={{ my: -0.75 }}
            />
          </Box>
        </Box>

        <Divider sx={{ my: 0.5, marginBottom: 0.5 }} />

        <Box>
          <Typography
            variant="caption"
            sx={{
              color: "text.disabled",
              fontWeight: "fontWeightRegular",
              display: "block",
              textTransform: "uppercase",
              fontSize: "11px",
              letterSpacing: "0.5px",
              mb: 0.5,
            }}
          >
            Row Display
          </Typography>
          <RadioGroup
            value={currentRowHeight}
            onChange={(e) => onRowHeightChange(e.target.value)}
            sx={{ gap: 0.25, display: "flex", flexDirection: "column" }}
          >
            {Object.keys(rowHeightMapping).map((key) => (
              <FormControlLabel
                key={key}
                value={key}
                control={<Radio size="small" />}
                label={
                  <Typography
                    typography="s1"
                    fontWeight="fontWeightRegular"
                    sx={{ ml: 0.5 }}
                  >
                    {key}
                  </Typography>
                }
                sx={{ my: -0.75 }}
              />
            ))}
          </RadioGroup>
        </Box>
      </Box>
    </Popover>
  );
};

DisplayMenu.propTypes = {
  anchorEl: PropTypes.object,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  viewAllPrompts: PropTypes.bool.isRequired,
  onViewAllPromptsChange: PropTypes.func.isRequired,
  showAllColumns: PropTypes.bool.isRequired,
  onShowAllColumnsChange: PropTypes.func.isRequired,
  rowHeightMapping: PropTypes.object.isRequired,
  currentRowHeight: PropTypes.string.isRequired,
  onRowHeightChange: PropTypes.func.isRequired,
  hasAgentConfigs: PropTypes.bool,
};

DisplayMenu.defaultProps = {
  hasAgentConfigs: false,
};

export default DisplayMenu;
