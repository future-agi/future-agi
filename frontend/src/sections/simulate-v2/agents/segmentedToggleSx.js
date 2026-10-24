/*
  A ToggleButtonGroup styled as the product's segmented control — the same
  look as SegmentedTabs (src/components/tabs/tabs.jsx) on Evals' Single /
  Composite: a bordered grey track, the selected option a filled neutral pill.
  No brand colour: choosing an option isn't a highlight.
*/
export const segmentedToggleSx = (theme) => {
  return {
    p: "2px",
    gap: 0,
    border: "1px solid",
    borderColor: "divider",
    borderRadius: "8px",
    bgcolor: "background.neutral",
    "& .MuiToggleButtonGroup-grouped, & .MuiToggleButton-root": {
      m: 0,
      border: "0 !important",
      borderRadius: "6px !important",
      px: 1.5,
      py: 0,
      minHeight: 28,
      gap: 0.5,
      textTransform: "none",
      fontSize: 13,
      fontWeight: 500,
      color: "text.secondary",
      "&:hover": { bgcolor: "transparent", color: "text.primary" },
      "&.Mui-selected, &.Mui-selected:hover": {
        fontWeight: 600,
        color: "text.primary",
        bgcolor: "background.paper",
        boxShadow: `inset 0 0 0 1px ${theme.palette.divider}`,
      },
      "&.Mui-disabled": { color: "text.disabled" },
    },
  };
};
