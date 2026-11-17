import { Box, Typography } from "@mui/material";
import Section from "src/components/section";
import React from "react";
import PropTypes from "prop-types";

const ConnectorCard = ({ title, icon, onClick }) => {
  return (
    <Section
      variant="panel"
      sx={{
        display: "flex",
        flexDirection: "column",
        minWidth: "250px",
        gap: 1,
        minHeight: "88px",
        justifyContent: "center",
        alignItems: "center",
        cursor: "pointer",
        boxShadow: "none",
        border: "1px solid",
        borderColor: "border.default",
        borderRadius: "8px",
        p: 1.5,
      }}
      onClick={onClick}
    >
      <Box alt="logo" component="img" src={icon} width={28} height={28} />
      <Typography sx={{ fontSize: 13, fontWeight: 600 }} color="text.primary">
        {title}
      </Typography>
    </Section>
  );
};

ConnectorCard.propTypes = {
  title: PropTypes.string,
  icon: PropTypes.any,
  onClick: PropTypes.func,
};

export default ConnectorCard;
