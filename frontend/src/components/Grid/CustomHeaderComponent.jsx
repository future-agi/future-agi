import PropTypes from "prop-types";
import HeaderIcon from "./HeaderIcon";

const wrapperStyle = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: "8px",
  position: "relative",
  width: "100%",
  height: "100%",
  paddingLeft: "12px",
  paddingRight: "12px",
};
const textStyle = {
  fontSize: "12px",
  color: "text.primary",
  fontWeight: 500,
  lineHeight: 1.4,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const CustomHeaderComponent = (
  /** @type {{ displayName: string }} */ { displayName },
) => {
  return (
    <div style={wrapperStyle}>
      <HeaderIcon displayName={displayName} />
      <span style={textStyle}>{displayName}</span>
    </div>
  );
};

export default CustomHeaderComponent;

CustomHeaderComponent.propTypes = {
  displayName: PropTypes.string,
};
