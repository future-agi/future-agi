import { Box, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useId } from "react";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";

// Icons that are multi-colored logos and should stay as <img>
const IMG_ONLY_ICONS = ["hugging_face"];

/**
 * One way to fill a dataset (DL#3, DL#26): a keyboard-reachable card with
 * an outcome-first subtitle, a chevron on hover and an optional `Docs ↗`.
 */
const DatasetOptions = ({
  title,
  subTitle,
  onClick,
  icons,
  iconify,
  docs,
  docsLabel,
  disabled = false,
  disabledReason,
}) => {
  const reasonId = useId();
  return (
    <Box
      role="button"
      tabIndex={0}
      aria-disabled={disabled || undefined}
      aria-describedby={disabled && disabledReason ? reasonId : undefined}
      onClick={disabled ? undefined : onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          if (!disabled) onClick?.();
        }
      }}
      sx={{
        px: 2,
        py: 1.5,
        display: "flex",
        alignItems: "center",
        gap: 2,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        cursor: disabled ? "not-allowed" : "pointer",
        outline: "none",
        "& .opt-chevron": { opacity: 0, transition: "opacity 120ms" },
        "&:hover, &:focus-visible": {
          backgroundColor: "background.neutral",
          borderColor: "primary.main",
          "& .opt-chevron": { opacity: disabled ? 0 : 1 },
        },
        "&:focus-visible": {
          boxShadow: (t) => `0 0 0 2px ${t.palette.primary.main}`,
        },
      }}
    >
      {iconify && !icons && (
        <Iconify
          icon={iconify}
          width={24}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      )}
      {icons && (
        <Box sx={{ flexShrink: 0, display: "flex" }}>
          {IMG_ONLY_ICONS.includes(icons) ? (
            <img
              src={`/icons/datasets/${icons}.svg`}
              alt=""
              width={24}
              height={24}
            />
          ) : (
            <SvgColor
              src={`/icons/datasets/${icons}.svg`}
              sx={{ width: 24, height: 24, color: "text.secondary" }}
            />
          )}
        </Box>
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <Typography
            fontWeight="fontWeightMedium"
            color="text.primary"
            variant="s1"
          >
            {title}
          </Typography>
        </Box>
        <Typography
          fontWeight="fontWeightRegular"
          color="text.secondary"
          variant="s2"
          component="div"
        >
          {subTitle}
        </Typography>
        {disabled && disabledReason && (
          <Typography
            id={reasonId}
            variant="s2"
            component="div"
            color="text.primary"
            sx={{ mt: 0.5 }}
          >
            {disabledReason}
          </Typography>
        )}
      </Box>
      {docs && (
        <Link
          href={docs}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          typography="s3"
          underline="hover"
          sx={{ whiteSpace: "nowrap", display: "inline-flex", gap: 0.25 }}
          aria-label={`${docsLabel ?? "Docs"} (opens in a new tab)`}
        >
          {docsLabel ?? "Docs"}
          <Iconify icon="mdi:arrow-top-right" width={12} />
        </Link>
      )}
      <Iconify
        className="opt-chevron"
        icon="mdi:chevron-right"
        width={18}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
    </Box>
  );
};

DatasetOptions.propTypes = {
  title: PropTypes.string,
  subTitle: PropTypes.string,
  onClick: PropTypes.func,
  icons: PropTypes.string,
  // an Iconify name, when there is no /icons/datasets svg
  iconify: PropTypes.string,
  docs: PropTypes.string,
  docsLabel: PropTypes.string,
  disabled: PropTypes.bool,
  disabledReason: PropTypes.string,
};

export default DatasetOptions;
