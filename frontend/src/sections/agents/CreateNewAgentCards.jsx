import React from "react";
import PropTypes from "prop-types";
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Typography,
  Box,
} from "@mui/material";
import Iconify from "src/components/iconify";

const CreateNewAgentCards = ({ title, subtitle, children }) => {
  return (
    <Accordion
      defaultExpanded
      disableGutters
      sx={{
        border: "1px solid",
        borderColor: "background.neutral",
        backgroundColor: "background.neutral",
        borderRadius: "4px !important",
        overflow: "hidden",
        "&:before": { display: "none" },
        "&.Mui-expanded": {
          margin: 0,
        },
      }}
    >
      <AccordionSummary
        expandIcon={
          <Iconify
            icon="line-md:chevron-up"
            width={22}
            height={22}
            color="text.primary"
          />
        }
        sx={{
          px: 2,
          py: 1.5,
          backgroundColor: "background.default",
          "&:hover": {
            backgroundColor: "background.default",
          },
          "&.Mui-expanded": {
            backgroundColor: "background.default",
            minHeight: "unset",
          },
          "& .MuiAccordionSummary-content": {
            margin: 0,
          },
          "& .MuiAccordionSummary-content.Mui-expanded": {
            margin: 0,
          },
        }}
      >
        <Box display={"flex"} flexDirection={"column"}>
          <Typography
            variant="m3"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {title}
          </Typography>
          <Typography
            variant="s2_1"
            fontWeight="fontWeightRegular"
            color="text.secondary"
          >
            {subtitle}
          </Typography>
        </Box>
      </AccordionSummary>

      <AccordionDetails
        sx={{
          px: 2,
          py: 0,
          pb: 2,
          backgroundColor: "background.default",
        }}
      >
        <Box
          display="flex"
          flexDirection="column"
          gap={2}
          bgcolor={"background.paper"}
          p={1.5}
          py={3}
          borderRadius={"4px !important"}
          border={"1px solid"}
          borderColor={"background.neutral"}
        >
          {children}
        </Box>
      </AccordionDetails>
    </Accordion>
  );
};

CreateNewAgentCards.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string.isRequired,
  children: PropTypes.node,
};

export default CreateNewAgentCards;
