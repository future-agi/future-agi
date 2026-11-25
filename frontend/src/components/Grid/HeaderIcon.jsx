import SvgColor from "../svg-color";
import Iconify from "../iconify";
import PropTypes from "prop-types";

const iconSize = { width: 20, height: 20 };
const HeaderIcon = ({ displayName }) => {
  if (displayName === "Annotation Name") {
    return (
      <SvgColor
        src="/assets/icons/ic_annotate_label.svg"
        sx={{ ...iconSize }}
      />
    );
  }
  if (displayName === "Updated At") {
    return <Iconify sx={{ ...iconSize }} icon="solar:clock-circle-linear" />;
  }
  return (
    <SvgColor
      src="/assets/icons/ic_col_header.svg"
      sx={{ ...iconSize, bgcolor: "text.primary" }}
    />
  );
};

export default HeaderIcon;
HeaderIcon.propTypes = {
  displayName: PropTypes.string,
};
