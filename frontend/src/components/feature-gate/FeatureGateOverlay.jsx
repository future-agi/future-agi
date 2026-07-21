import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Paper,
  Skeleton,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

const PreviewSkeleton = () => (
  <Stack spacing={2} sx={{ p: 3, height: 1 }}>
    <Stack direction="row" spacing={2} alignItems="center">
      <Skeleton variant="rounded" width={220} height={26} />
      <Box sx={{ flex: 1 }} />
      <Skeleton variant="rounded" width={90} height={28} />
      <Skeleton variant="rounded" width={90} height={28} />
    </Stack>
    <Skeleton variant="rounded" height={92} />
    <Stack direction="row" spacing={2}>
      <Skeleton variant="rounded" height={120} sx={{ flex: 1 }} />
      <Skeleton variant="rounded" height={120} sx={{ flex: 1 }} />
      <Skeleton variant="rounded" height={120} sx={{ flex: 1 }} />
      <Skeleton variant="rounded" height={120} sx={{ flex: 1 }} />
    </Stack>
    <Stack direction="row" spacing={2} sx={{ flex: 1 }}>
      <Skeleton variant="rounded" sx={{ width: 300 }} />
      <Skeleton variant="rounded" sx={{ flex: 1 }} />
    </Stack>
  </Stack>
);

const FeatureGateOverlay = ({
  image,
  imageDark,
  imageAlt = "Feature preview",
  eyebrow = "Cloud feature",
  title,
  description,
  steps = [],
  primaryLabel = "Contact us to upgrade",
  primaryHref,
  onPrimary,
  secondaryLabel,
  secondaryHref,
  footnote,
  blur = 9,
  blurFrom = "38%",
  minHeight = 480,
  maxCardWidth = 480,
  children,
  sx,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const resolvedImage = isDark ? imageDark || image : image;
  const usingSkeleton = !children && !resolvedImage;
  const effectiveBlurFrom = usingSkeleton ? 0 : blurFrom;

  let backdrop = <PreviewSkeleton />;
  if (children) {
    backdrop = children;
  } else if (resolvedImage) {
    backdrop = (
      <Box
        component="img"
        src={resolvedImage}
        alt={imageAlt}
        draggable={false}
        sx={{
          display: "block",
          width: "100%",
          height: "100%",
          minHeight,
          objectFit: "cover",
          objectPosition: "top center",
        }}
      />
    );
  }

  return (
    <Box
      sx={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight,
        overflow: "hidden",
        borderRadius: 2,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        ...sx,
      }}
    >
      <Box
        aria-hidden
        sx={{
          width: "100%",
          height: "100%",
          minHeight,
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        {backdrop}
      </Box>

      <Box
        aria-hidden
        sx={{
          position: "absolute",
          left: 0,
          right: 0,
          top: effectiveBlurFrom,
          bottom: 0,
          backdropFilter: `blur(${blur}px)`,
          WebkitBackdropFilter: `blur(${blur}px)`,
          background: isDark
            ? `linear-gradient(to bottom, ${alpha(
                theme.palette.background.default,
                0.35,
              )}, ${alpha(theme.palette.background.default, 0.75)})`
            : `linear-gradient(to bottom, ${alpha(
                theme.palette.common.white,
                0.35,
              )}, ${alpha(theme.palette.grey[300], 0.6)})`,
          pointerEvents: "none",
        }}
      />

      <Box
        sx={{
          position: "absolute",
          inset: 0,
          zIndex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          p: 3,
        }}
      >
        <Paper
          elevation={12}
          sx={{
            width: "100%",
            maxWidth: maxCardWidth,
            p: 3.5,
            borderRadius: 2,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack spacing={2.5}>
            {eyebrow && (
              <Box
                sx={{
                  alignSelf: "flex-start",
                  px: 1,
                  py: 0.25,
                  borderRadius: 0.75,
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  color: "primary.main",
                  bgcolor: alpha(theme.palette.primary.main, 0.1),
                  border: "1px solid",
                  borderColor: alpha(theme.palette.primary.main, 0.22),
                }}
              >
                {eyebrow}
              </Box>
            )}

            {title && (
              <Typography variant="h5" fontWeight="fontWeightBold">
                {title}
              </Typography>
            )}

            {description && (
              <Typography variant="body2" color="text.secondary">
                {description}
              </Typography>
            )}

            {steps.length > 0 && (
              <Stack spacing={1.5}>
                <Typography
                  variant="overline"
                  color="text.disabled"
                  sx={{ fontWeight: 700 }}
                >
                  What to do next
                </Typography>
                {steps.map((step, index) => (
                  <Stack
                    key={step}
                    direction="row"
                    spacing={1.5}
                    alignItems="flex-start"
                  >
                    <Box
                      sx={{
                        width: 22,
                        height: 22,
                        flexShrink: 0,
                        borderRadius: "50%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 12,
                        fontWeight: 700,
                        color: "primary.main",
                        bgcolor: alpha(theme.palette.primary.main, 0.1),
                        border: "1px solid",
                        borderColor: alpha(theme.palette.primary.main, 0.22),
                      }}
                    >
                      {index + 1}
                    </Box>
                    <Typography variant="body2" color="text.primary">
                      {step}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}

            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              justifyContent="flex-end"
            >
              {secondaryLabel && secondaryHref && (
                <Button
                  color="inherit"
                  href={secondaryHref}
                  target="_blank"
                  rel="noopener"
                  sx={{ color: "text.secondary" }}
                >
                  {secondaryLabel}
                </Button>
              )}
              <Button
                variant="contained"
                color="primary"
                onClick={onPrimary}
                href={onPrimary ? undefined : primaryHref}
                target={onPrimary ? undefined : "_blank"}
                rel={onPrimary ? undefined : "noopener"}
                endIcon={<Iconify icon="solar:arrow-right-up-linear" />}
              >
                {primaryLabel}
              </Button>
            </Stack>

            {footnote && (
              <Stack
                direction="row"
                spacing={1}
                alignItems="flex-start"
                sx={{ pt: 2, borderTop: "1px solid", borderColor: "divider" }}
              >
                <Iconify
                  icon="solar:info-circle-linear"
                  width={16}
                  sx={{ color: "text.disabled", flexShrink: 0, mt: "2px" }}
                />
                <Typography variant="caption" color="text.disabled">
                  {footnote}
                </Typography>
              </Stack>
            )}
          </Stack>
        </Paper>
      </Box>
    </Box>
  );
};

FeatureGateOverlay.propTypes = {
  image: PropTypes.string,
  imageDark: PropTypes.string,
  imageAlt: PropTypes.string,
  eyebrow: PropTypes.string,
  title: PropTypes.string,
  description: PropTypes.node,
  steps: PropTypes.arrayOf(PropTypes.string),
  primaryLabel: PropTypes.string,
  primaryHref: PropTypes.string,
  onPrimary: PropTypes.func,
  secondaryLabel: PropTypes.string,
  secondaryHref: PropTypes.string,
  footnote: PropTypes.node,
  blur: PropTypes.number,
  blurFrom: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  minHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  maxCardWidth: PropTypes.number,
  children: PropTypes.node,
  sx: PropTypes.object,
};

export default FeatureGateOverlay;
