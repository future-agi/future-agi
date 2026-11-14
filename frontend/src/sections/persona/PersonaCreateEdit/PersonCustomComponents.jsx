import React from "react";
import { styled } from "@mui/material/styles";
import {
  Accordion as MuiAccordion,
  AccordionSummary as MuiAccordionSummary,
  AccordionDetails as MuiAccordionDetails,
} from "@mui/material";

// Simple Custom Accordion
const CustomPersonaAccordion = styled(MuiAccordion)(({ theme }) => ({
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.spacing(1),
  marginBottom: theme.spacing(1),
  overflow: "hidden",
  "&:not(:last-child)": {
    borderBottom: 0,
  },
  "&::before": {
    display: "none",
  },
  "&.Mui-expanded": {
    margin: `0 0`,
  },
}));

// Simple Custom Accordion Header
const CustomPersonaAccordionHeader = styled(MuiAccordionSummary)(
  ({ theme }) => ({
    backgroundColor: theme.palette.background.paper,
    padding: theme.spacing(2, 2),
    "& .MuiAccordionSummary-expandIconWrapper": {
      color: theme.palette.text.secondary,
    },
    "& .MuiAccordionSummary-content": {
      margin: `0 0`,
      "&.Mui-expanded": {
        margin: `0 0`,
      },
      ...theme.typography.m3,
      fontWeight: theme.typography.fontWeightSemiBold,
    },
  }),
);

// Simple Custom Accordion Content
const CustomPersonaAccordionContent = styled(MuiAccordionDetails)(
  ({ theme }) => ({
    padding: theme.spacing(2),
    paddingTop: 0,
    backgroundColor: theme.palette.background.paper,
  }),
);

// Export components
export {
  CustomPersonaAccordion,
  CustomPersonaAccordionHeader,
  CustomPersonaAccordionContent,
};
