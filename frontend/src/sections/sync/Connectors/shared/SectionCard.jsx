import { Box, Button, Divider, Typography } from "@mui/material";
import Section from "src/components/section";
import React from "react";
import PropTypes from "prop-types";

const SectionCard = ({
  title,
  children,
  onActionButtonClick,
  actionButtonText,
}) => {
  return (
    <Section variant="panel" sx={{ display: "flex", flexDirection: "column" }}>
      <Box
        sx={{
          padding: "20px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography fontWeight={600} fontSize={13} color="text.primary">
          {title}
        </Typography>
        {onActionButtonClick ? (
          <Button variant="soft" size="small" onClick={onActionButtonClick}>
            {actionButtonText}
          </Button>
        ) : null}
      </Box>
      <Divider />
      {children}
    </Section>
  );
};

SectionCard.propTypes = {
  title: PropTypes.string,
  children: PropTypes.any,
  onActionButtonClick: PropTypes.func,
  actionButtonText: PropTypes.string,
};

export default SectionCard;
