import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import {
  NOT_IN_RELEASE,
  NOT_IN_RELEASE_SHORT,
  visuallyHidden,
} from "./notInRelease";

// A quiet neutral tag on a part that is not built in this release. Kept
// low-emphasis (action.selected fill, secondary text) so it reads as state,
// not a call to action. The visible text is the shared line, so it is also
// what a screen reader reads.
//
// `short`, where the whole line cannot fit (three disabled tabs in one
// strip): the chip shows the short word, a screen reader still reads the
// whole line, and the whole line shows in a tooltip on hover. The chip takes
// the pointer itself, so its tooltip works inside a disabled tab, which
// takes none.
export default function NotInReleaseChip({ sx, short = false }) {
  const chip = (
    <Box
      component="span"
      data-not-in-release=""
      sx={{
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        px: 0.75,
        height: 18,
        borderRadius: "4px",
        typography: "s3",
        fontWeight: 500,
        whiteSpace: "nowrap",
        color: "text.secondary",
        bgcolor: "action.selected",
        ...(short && { pointerEvents: "auto", cursor: "default" }),
        ...sx,
      }}
    >
      {short ? (
        <>
          <span aria-hidden="true">{NOT_IN_RELEASE_SHORT}</span>
          <Box component="span" sx={visuallyHidden}>
            {NOT_IN_RELEASE}
          </Box>
        </>
      ) : (
        NOT_IN_RELEASE
      )}
    </Box>
  );
  if (!short) return chip;
  return (
    <CustomTooltip
      show
      // A node, not a string: MUI then adds no aria-label or title to the
      // chip, whose hidden text already says the line.
      title={<>{NOT_IN_RELEASE}</>}
      describeChild
      placement="top"
      arrow
      size="small"
    >
      {chip}
    </CustomTooltip>
  );
}
NotInReleaseChip.propTypes = { sx: PropTypes.object, short: PropTypes.bool };
