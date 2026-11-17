// Keep the existing two-panel desktop layout at its former 1200px minimum.
// Below it, stack both panels without hiding content or clipping overflow.
export const authSplitSx = {
  width: "100%",
  minWidth: 0,
  height: { xs: "auto", lg: "100vh" },
  display: "flex",
  flexDirection: { xs: "column", lg: "row" },
};

export const authFormPanelSx = {
  width: { xs: "100%", lg: "50%" },
  minWidth: 0,
  height: { xs: "auto", lg: "100vh" },
};

export const authAsideSx = {
  width: { xs: "100%", lg: "50%" },
  minWidth: 0,
  height: { xs: "auto", lg: "100%" },
  backgroundColor: "background.neutral",
};

export const authFormGutterSx = {
  px: { xs: 2, sm: 4, lg: 10 },
};
