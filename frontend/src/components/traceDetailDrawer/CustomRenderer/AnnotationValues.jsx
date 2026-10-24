import { Box, Typography } from "@mui/material";
import React from "react";
import Iconify from "../../iconify";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

const AnnotationValues = ({ value, annotationType, maxChips, settings }) => {
  // Categorical values read as plain text joined with " · "; the overflow
  // count is a quiet qualifier.
  const renderCategorical = () => {
    const categoricalValues = value || [];
    const shown =
      maxChips === undefined
        ? categoricalValues
        : categoricalValues?.slice(0, maxChips);
    const overflow =
      maxChips !== undefined && categoricalValues?.length > maxChips
        ? categoricalValues.length - maxChips
        : 0;

    return (
      <Typography sx={{ fontSize: 13, color: "text.primary" }} noWrap>
        {shown?.join(" · ")}
        {overflow > 0 && (
          <Box
            component="span"
            sx={{
              color: "text.tertiary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {` · +${overflow}`}
          </Box>
        )}
      </Typography>
    );
  };

  if (!value || value === "") {
    return <Box>-</Box>;
  }

  if (annotationType === "number" || annotationType === "text") {
    return <Box>{value}</Box>;
  }

  if (annotationType === "thumbs_up_down") {
    return (
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, height: "100%" }}
      >
        <Iconify
          icon="solar:like-linear"
          color={value === "up" ? "status.success.fg" : "divider"}
        />
        <Iconify
          icon="solar:dislike-linear"
          color={value === "down" ? "status.danger.fg" : "divider"}
        />
      </Box>
    );
  }

  if (annotationType === "star") {
    return (
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, height: "100%" }}
      >
        {Array.from({ length: parseInt(value) }).map((_, index) => (
          <Iconify
            key={index}
            icon="solar:star-bold"
            sx={{ fontSize: "inherit", flexShrink: 0 }}
            width={25}
            color="status.warning.fg"
          />
        ))}
        <ShowComponent condition={settings?.noOfStars !== undefined}>
          {Array.from({
            length: parseInt(settings?.noOfStars) - parseInt(value),
          }).map((_, index) => (
            <Iconify
              key={index}
              icon="solar:star-linear"
              width={25}
              sx={{
                fontSize: "inherit",
                color: "text.secondary",
                flexShrink: 0,
              }}
            />
          ))}
        </ShowComponent>
      </Box>
    );
  }

  if (annotationType === "categorical") {
    return (
      <Box
        sx={{ display: "flex", gap: 1, height: "100%", alignItems: "center" }}
      >
        {renderCategorical()}
      </Box>
    );
  }

  return <Box>{value}</Box>;
};

AnnotationValues.propTypes = {
  value: PropTypes.string,
  annotationType: PropTypes.string,
  maxChips: PropTypes.any,
  settings: PropTypes.object,
};

export default AnnotationValues;
