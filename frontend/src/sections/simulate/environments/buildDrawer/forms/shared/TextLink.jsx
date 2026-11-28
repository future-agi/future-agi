import PropTypes from "prop-types";
import { ButtonBase } from "@mui/material";
import { T12, focusRing } from "../../drawerStyles";

/** The quiet "do it another way" link the setup forms use. */
export default function TextLink({ onClick, children, sx }) {
  return (
    <ButtonBase
      onClick={onClick}
      sx={{
        ...T12,
        color: "text.secondary",
        fontWeight: 500,
        width: "fit-content",
        justifyContent: "flex-start",
        textAlign: "left",
        borderRadius: "4px",
        "&:hover": { color: "text.primary", textDecoration: "underline" },
        "&.Mui-focusVisible": focusRing,
        ...sx,
      }}
    >
      {children}
    </ButtonBase>
  );
}
TextLink.propTypes = {
  onClick: PropTypes.func,
  children: PropTypes.node,
  sx: PropTypes.object,
};
