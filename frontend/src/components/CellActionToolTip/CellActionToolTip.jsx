import { Tooltip, styled, tooltipClasses } from "@mui/material";

const CellActionToolTip = styled(({ className, ...props }) => (
  <Tooltip {...props} classes={{ popper: className }} />
))(({ theme }) => ({
  [`& .${tooltipClasses.tooltip}`]: {
    // Not a dark tooltip: it only carries action buttons on the page surface,
    // so content takes the page text colour (the theme makes everything in a
    // tooltip inherit the tooltip's near-white text).
    color: theme.palette.text.primary,
    padding: 0,
    margin: 0,
    minWidth: 0,
    maxWidth: "none",
    width: "auto",
    height: "auto",
    background: "transparent",
    backgroundColor: "transparent",
    boxShadow: "none",
    border: "none",
    borderRadius: 0,
    lineHeight: 0,
    overflow: "visible",
    transition: "all 0.2s ease-in-out",
    marginLeft: "-44px !important",
    cursor: "pointer",
  },
}));

export default CellActionToolTip;
