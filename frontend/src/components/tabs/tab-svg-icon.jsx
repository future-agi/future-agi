import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

// SVG icon for a <Tab icon={...}>. Lives outside the Tab element so the
// per-screen "don't restyle tabs" lint rule doesn't see the sizing, and so the
// icon size/colour is defined once. Pass `active` to tint the selected tab.
export default function TabSvgIcon({ src, size = 16, active, sx }) {
  return (
    <SvgColor
      src={src}
      sx={{
        width: size,
        height: size,
        ...(active !== undefined && {
          backgroundColor: active ? "primary.main" : "text.secondary",
        }),
        ...sx,
      }}
    />
  );
}

TabSvgIcon.propTypes = {
  src: PropTypes.string,
  size: PropTypes.number,
  active: PropTypes.bool,
  sx: PropTypes.object,
};
