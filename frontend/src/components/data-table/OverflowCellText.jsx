import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

// The text is cut when it is wider than its box. scrollWidth rounds to whole
// pixels, so a sub-pixel overflow (which still draws the ellipsis) is caught
// by the text's own width.
function isCut(el) {
  if (!el) return false;
  if (el.scrollWidth > el.clientWidth) return true;
  const range = document.createRange?.();
  if (!range?.getBoundingClientRect) return false;
  range.selectNodeContents(el);
  return range.getBoundingClientRect().width > el.getBoundingClientRect().width;
}

/**
 * One line of table cell text that ends in an ellipsis when its column is
 * narrow (a name at 1024 px). The whole text shows in a tooltip when it is
 * cut, on hover and while its grid cell holds focus (the grid's arrow keys
 * move focus from cell to cell), so a cut name reads without a mouse. A
 * screen reader always gets the whole text: it is all in the DOM.
 *
 * `detail` is a second tooltip line (Connected agents: the description); with
 * it the tooltip shows even when the text fits.
 */
export default function OverflowCellText({ text, detail, sx }) {
  const ref = useRef(null);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [cut, setCut] = useState(false);

  // DataGrid moves focus to the cell element itself, not into it.
  useEffect(() => {
    const cell = ref.current?.closest?.(".MuiDataGrid-cell");
    if (!cell) return undefined;
    const onFocus = () => {
      setCut(isCut(ref.current));
      setFocused(true);
    };
    const onBlur = () => setFocused(false);
    cell.addEventListener("focus", onFocus);
    cell.addEventListener("blur", onBlur);
    return () => {
      cell.removeEventListener("focus", onFocus);
      cell.removeEventListener("blur", onBlur);
    };
  }, []);

  const open = (hovered || focused) && (cut || Boolean(detail));
  const title = detail ? (
    <>
      <Box component="span" sx={{ display: "block", fontWeight: 500 }}>
        {text}
      </Box>
      <Box component="span" sx={{ display: "block" }}>
        {detail}
      </Box>
    </>
  ) : (
    text
  );

  return (
    <CustomTooltip
      show
      open={open}
      title={title}
      placement="top"
      arrow
      size="small"
      // Opened from the hover and focus handlers here, only when it helps.
      describeChild
      disableHoverListener
      disableFocusListener
      disableTouchListener
    >
      <Typography
        ref={ref}
        variant="body2"
        noWrap
        onMouseEnter={() => {
          setCut(isCut(ref.current));
          setHovered(true);
        }}
        onMouseLeave={() => setHovered(false)}
        sx={sx}
      >
        {text}
      </Typography>
    </CustomTooltip>
  );
}

OverflowCellText.propTypes = {
  text: PropTypes.node,
  detail: PropTypes.node,
  sx: PropTypes.object,
};
