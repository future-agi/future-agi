// Every Hub tab row scrolls sideways when its tabs do not fit (a 390 px phone: the
// main nav, the review nav, the review page's tabs). A row that is cut at the edge
// must say so (HUB-22: the nav "ends at Se"), so it shows the platform's chevrons,
// as the trace drawer and the voice drawer do: only while the row overflows
// (`auto`), on phones too, the one with nowhere to go dimmed rather than hidden.
export const TAB_ROW_PROPS = Object.freeze({
  variant: "scrollable",
  scrollButtons: "auto",
  allowScrollButtonsMobile: true,
});

export const TAB_ROW_SCROLL_SX = Object.freeze({
  "& .MuiTabs-scrollButtons": {
    width: 24,
    flexShrink: 0,
    "&.Mui-disabled": { opacity: 0.3 },
  },
});
