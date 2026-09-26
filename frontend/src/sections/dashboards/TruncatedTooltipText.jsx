import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { DESCRIPTION_TOOLTIP_SX } from "./constants";
import useIsTruncated from "./hooks/useIsTruncated";

/** Reveals a description in a tooltip only once its single line is clipped.
 *  Children is a render prop taking the ref to measure, so each caller keeps
 *  its own markup while the measure-and-reveal wiring stays in one place. */
export default function TruncatedTooltipText({ text, children }) {
  const [measureRef, isTruncated] = useIsTruncated(text);

  return (
    <CustomTooltip
      show={isTruncated}
      title={text}
      size="small"
      placement="bottom-start"
      slotProps={{ tooltip: { sx: DESCRIPTION_TOOLTIP_SX } }}
    >
      {children(measureRef)}
    </CustomTooltip>
  );
}

TruncatedTooltipText.propTypes = {
  text: PropTypes.string.isRequired,
  children: PropTypes.func.isRequired,
};
