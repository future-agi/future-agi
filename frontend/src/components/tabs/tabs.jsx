import { styled, Tabs } from "@mui/material";

// The underline tabs are styled once, in the theme (overrides/components/
// tabs.js): 32px, 13px/500, grey at rest, fg.default when selected, accent
// underline. CustomTabs adds nothing on top, so every tab strip matches.
export const CustomTabs = styled(Tabs)({});

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
))(({ theme }) => segmentedTrack(theme));

// Design system segmented control (.ds-seg) — shared with the theme's
// ToggleButtonGroup override so every two-to-four-way switch looks the same:
// a bg.muted track with 2px padding, 24px options in 12px/500, the selected
// one a surface pill with a hairline ring.
export const segmentedTrack = (theme) => ({
  minHeight: 28,
  width: "fit-content",
  padding: 2,
  borderRadius: 6,
  backgroundColor: theme.palette.background.muted,
  "& .MuiTabs-flexContainer": { gap: 2 },
  "& .MuiTab-root": {
    minHeight: 24,
    height: 24,
    minWidth: 0,
    padding: "0 10px",
    marginRight: "0 !important",
    textTransform: "none",
    fontSize: 12,
    lineHeight: "24px",
    fontWeight: 500,
    borderRadius: 4,
    color: theme.palette.text.secondary,
    "&:hover:not(.Mui-selected)": { color: theme.palette.text.primary },
    "&.Mui-selected": {
      color: theme.palette.text.primary,
      backgroundColor: theme.palette.background.paper,
      boxShadow: `0 0 0 1px ${theme.palette.border?.default || theme.palette.divider}`,
    },
  },
});
