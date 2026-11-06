import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import _ from "lodash";
import PersonaComponent from "src/components/persona/personaComponent";
import CustomTooltip from "src/components/tooltip";

const PersonaCellRenderer = ({ value }) => {
  const formattedValue = useMemo(() => {
    if (!value) return {};
    try {
      if (typeof value === "string") {
        return JSON.parse(value);
      }
      return value;
    } catch (e) {
      return {};
    }
  }, [value]);

  return (
    <CustomTooltip
      card
      show={true}
      arrow={true}
      title={
        <Box display={"flex"} flexDirection={"column"} gap={2}>
          {" "}
          <Typography typography={"s2_1"} fontWeight={"fontWeightSemiBold"}>
            Persona
          </Typography>
          <PersonaComponent formattedValue={formattedValue} />
        </Box>
      }
    >
      <Box
        sx={{
          display: "flex",
          gap: "10px",
          flexWrap: "wrap",
          lineHeight: "1.5",
          padding: 1,
          height: "100%",
          overflowY: "auto",
          overflowX: "hidden",
          alignItems: "center",
        }}
      >
        <PersonaComponent formattedValue={formattedValue} />
      </Box>
    </CustomTooltip>
  );
};

PersonaCellRenderer.propTypes = {
  value: PropTypes.any,
};

export default React.memo(PersonaCellRenderer);
