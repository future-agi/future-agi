import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  IconButton,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

AnnotateFooter.propTypes = {
  currentPosition: PropTypes.number.isRequired,
  total: PropTypes.number.isRequired,
  onPrev: PropTypes.func.isRequired,
  onNext: PropTypes.func.isRequired,
  hasPrev: PropTypes.bool.isRequired,
  hasNext: PropTypes.bool.isRequired,
  isLoadingPrev: PropTypes.bool,
  isLoadingNext: PropTypes.bool,
};

export default function AnnotateFooter({
  currentPosition,
  total,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  isLoadingPrev = false,
  isLoadingNext = false,
}) {
  const isLoading = isLoadingPrev || isLoadingNext;
  const safeTotal = Math.max(total, 1);
  const safePos = Math.min(Math.max(currentPosition, 0), safeTotal);
  const progress = total > 0 ? (safePos / safeTotal) * 100 : 0;

  return (
    <Box
      sx={{
        position: "relative",
        flexShrink: 0,
        borderTop: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      {/* Thin progress bar: switches to indeterminate animation while a
          prev/next fetch is in flight so the loading state is obvious
          even when the user's eye isn't on a specific button. */}
      <LinearProgress
        variant={isLoading ? "indeterminate" : "determinate"}
        value={progress}
        sx={{
          height: 2,
          bgcolor: "transparent",
          "& .MuiLinearProgress-bar": {
            transition: "transform 200ms ease-out",
          },
        }}
      />
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="center"
        spacing={1.5}
        sx={{ px: 2, py: 0.5, minHeight: 36 }}
      >
        <Tooltip title="Previous (←)" placement="top" arrow disableInteractive>
          <span>
            <IconButton
              size="small"
              disabled={!hasPrev || isLoading}
              onClick={onPrev}
              aria-label="Previous"
              sx={{
                p: 0.5,
                color: "text.primary",
                "&:hover": {
                  bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
                  color: "primary.main",
                },
                "&.Mui-disabled": {
                  color: "text.disabled",
                  opacity: 0.5,
                },
              }}
            >
              {isLoadingPrev ? (
                <CircularProgress size={14} thickness={5} />
              ) : (
                <Iconify icon="eva:arrow-ios-back-fill" width={18} />
              )}
            </IconButton>
          </span>
        </Tooltip>
        <Typography
          variant="caption"
          sx={{
            minWidth: 80,
            textAlign: "center",
            userSelect: "none",
            fontSize: 13,
            fontWeight: 600,
            color: "text.primary",
            lineHeight: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {safePos}
          <Box
            component="span"
            sx={{
              color: "text.disabled",
              fontWeight: 400,
              mx: 0.75,
            }}
          >
            /
          </Box>
          <Box
            component="span"
            sx={{ color: "text.secondary", fontWeight: 500 }}
          >
            {total}
          </Box>
        </Typography>
        <Tooltip title="Next (→)" placement="top" arrow disableInteractive>
          <span>
            <IconButton
              size="small"
              disabled={!hasNext || isLoading}
              onClick={onNext}
              aria-label="Next"
              sx={{
                p: 0.5,
                color: "text.primary",
                "&:hover": {
                  bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
                  color: "primary.main",
                },
                "&.Mui-disabled": {
                  color: "text.disabled",
                  opacity: 0.5,
                },
              }}
            >
              {isLoadingNext ? (
                <CircularProgress size={14} thickness={5} />
              ) : (
                <Iconify icon="eva:arrow-ios-forward-fill" width={18} />
              )}
            </IconButton>
          </span>
        </Tooltip>
      </Stack>
    </Box>
  );
}
