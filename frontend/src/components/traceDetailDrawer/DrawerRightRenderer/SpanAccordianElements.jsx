import React from "react";
import {
  Accordion as MuiAccordion,
  AccordionSummary as MuiAccordionSummary,
  AccordionDetails as MuiAccordionDetails,
  styled,
  alpha,
} from "@mui/material";
import Iconify from "src/components/iconify";

export const Accordion = styled((props) => (
  <MuiAccordion disableGutters elevation={0} square {...props} />
))(({ theme }) => ({
  border: `1px solid ${alpha(theme.palette.text.disabled, 0.2)}`,
  borderRadius: theme.spacing(1),
  "&::before": {
    display: "none",
  },
  "&.Mui-expanded": {
    boxShadow: "none",
  },
}));

export const AccordionSummary = styled((props) => (
  <MuiAccordionSummary
    expandIcon={
      <Iconify
        icon="solar:alt-arrow-right-linear"
        sx={{ color: "text.primary" }}
        width={16}
      />
    }
    {...props}
  />
))(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  borderRadius: "8px",
  flexDirection: "row-reverse",
  fontWeight: 500,
  fontSize: "13px",
  color: "text.primary",
  "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
    transform: "rotate(90deg)",
  },
  "& .MuiAccordionSummary-content": {
    marginLeft: theme.spacing(1),
  },
}));

export const AccordionDetails = styled((props) => (
  <MuiAccordionDetails {...props} />
))(({ theme }) => ({
  paddingX: theme.spacing(1),
  paddingY: 0,
}));
