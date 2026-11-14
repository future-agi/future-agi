import { Box, Typography } from "@mui/material";
import React from "react";
import SvgColor from "../svg-color";
import PropTypes from "prop-types";
import _ from "lodash";
import { getPersonaIconAndLabel } from "./common";
const PersonaChip = ({ label, icon }) => {
  return (
    <Box
      sx={{
        paddingX: "6px",
        paddingY: "2px",
        backgroundColor: "background.neutral",

        gap: "6px",
        display: "flex",
        alignItems: "flex-start",
      }}
    >
      <SvgColor
        src={icon}
        sx={{
          width: "16px",
          height: "16px",
          color: "status.info.fg",
          flexShrink: 0,
          mt: "2px",
        }}
      />
      <Typography
        typography="s2_1"
        sx={{
          overflow: "hidden",
          wordBreak: "break-word",
          whiteSpace: "normal",
        }}
      >
        {label}
      </Typography>
    </Box>
  );
};

PersonaChip.propTypes = {
  label: PropTypes.string,
  icon: PropTypes.string,
};

const PersonaComponent = ({ formattedValue = {} }) => {
  let parsed = formattedValue;

  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed.replace(/'/g, '"'));
    } catch {
      const { label, icon } = getPersonaIconAndLabel("name");
      return (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
          <PersonaChip label={`${label}: ${parsed}`} icon={icon} />
        </Box>
      );
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      {Object.entries(parsed || {}).map(([key, value]) => {
        const camelCasedKey = _.camelCase(key.replace(/_/g, " "));
        const personaSetting = getPersonaIconAndLabel(camelCasedKey);

        // Handle array values
        let displayValue = value;
        if (Array.isArray(value)) {
          displayValue = value.join(", ");
        } else if (typeof value === "object" && value !== null) {
          displayValue = JSON.stringify(value);
        } else {
          displayValue = _.capitalize(value);
        }
        if (!displayValue) {
          return; //return when voice persona characteristic value is null or undefined which come in chat persona
        }

        return (
          <PersonaChip
            key={key}
            label={`${personaSetting.label}: ${displayValue}`}
            icon={personaSetting.icon}
          />
        );
      })}
    </Box>
  );
};

export default PersonaComponent;

PersonaComponent.propTypes = {
  formattedValue: PropTypes.object,
};
