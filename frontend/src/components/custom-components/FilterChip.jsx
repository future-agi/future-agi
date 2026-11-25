import { Chip, styled } from "@mui/material";
import React from "react";

// FilterChip component
export const FilterChip = styled(Chip)(({ theme }) => ({
  backgroundColor: theme.palette.action.hover,
  color: theme.palette.primary.main,
  "&:hover": {
    backgroundColor: theme.palette.action.selected,
  },
  "& .MuiChip-deleteIcon": {
    color: theme.palette.primary.light,
    "&:hover": {
      color: theme.palette.primary.main,
      backgroundColor: "transparent",
    },
  },
}));

// SortIcon component
export const SortIcon = () => (
  <svg
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <path
      d="M3 18H9V16H3V18ZM3 6V8H21V6H3ZM3 13H15V11H3V13Z"
      fill="currentColor"
    />
  </svg>
);
