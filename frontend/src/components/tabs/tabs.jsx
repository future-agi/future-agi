import { styled, Tabs } from "@mui/material";

export const CustomTabs = styled(Tabs)(({ theme }) => ({
  "& .MuiTab-root": {
    color: theme.palette.text.disabled,
    ...theme.typography.s2,
    fontWeight: 500,
    "&.Mui-selected": {
      color: theme.palette.primary.main,
      fontWeight: 600,
    },
  },
  "& .MuiTabs-indicator": {
    backgroundColor: theme.palette.primary.main,
  },
}));

/**
 * Segmented tabs — the pill strip used on the eval detail page
 * (Eval Details / Usage / Feedback / Ground Truth).
 *
 * Lifted here rather than re-styled per screen: the same inline block was
 * pasted into a dozen call sites, so "our tab component" had no single
 * definition to point at. Takes plain <Tab> children like CustomTabs does.
 *
 * Reads as a control on its own — bordered container, filled pill on the
 * selected item — so it holds up in a card header where an underline
 * indicator has no baseline to sit on.
 */
export const SegmentedTabs = styled((props) => (
  <Tabs {...props} TabIndicatorProps={{ style: { display: "none" } }} />
))(({ theme }) => {
  const dark = theme.palette.mode === "dark";
  return {
    minHeight: 32,
    width: "fit-content",
    padding: "2px",
    border: "1px solid",
    borderColor: theme.palette.divider,
    borderRadius: "8px",
    backgroundColor: dark ? "rgba(255,255,255,0.04)" : "#f4f4f5",
    "& .MuiTabs-flexContainer": { gap: 0 },
    "& .MuiTab-root": {
      minHeight: 32,
      padding: theme.spacing(0, 1.5),
      marginRight: "0px !important",
      textTransform: "none",
      fontSize: "13px",
      fontWeight: 500,
      borderRadius: "6px",
      /*
        Use secondary rather than disabled here — text.disabled reads
        as "you can't click this" against a light pill background, so
        the inactive tab looked switched off. Secondary keeps enough
        contrast to read as an interactive control.
      */
      color: theme.palette.text.secondary,
      transition: theme.transitions.create(["background-color", "color"], {
        duration: theme.transitions.duration.shortest,
      }),
      "&:hover:not(.Mui-selected)": {
        color: theme.palette.text.primary,
      },
      "&.Mui-selected": {
        fontWeight: 600,
        color: theme.palette.text.primary,
        backgroundColor: dark ? "rgba(255,255,255,0.12)" : "#fff",
        boxShadow: dark ? "none" : "0 1px 3px rgba(0,0,0,0.08)",
      },
    },
  };
});
