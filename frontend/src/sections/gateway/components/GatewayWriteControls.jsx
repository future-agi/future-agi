import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Chip,
  IconButton,
  Slider,
  Switch,
  TextField,
  ToggleButtonGroup,
} from "@mui/material";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import GatewayAdminOnly from "./GatewayAdminOnly";

// Editor-only controls share the gateway's write permission and explanation.
// Read controls (tabs, search, disclosure buttons and Cancel) stay ordinary MUI.
/**
 * Preserve the wrapped MUI component's props and polymorphic ref signature.
 * @template {React.ElementType} T
 * @param {T} Control
 * @param {string} name
 * @returns {T}
 */
function writeControl(Control, name) {
  // JSX cannot resolve LibraryManagedAttributes for an uninstantiated generic.
  // The exported wrapper keeps T; only the internal render uses ElementType.
  const Component = /** @type {React.ElementType} */ (Control);
  const WriteControl = forwardRef(
    /** @param {React.ComponentProps<T>} props */ (props, ref) => {
      const { canEdit } = useGatewayAdmin();
      return (
        <GatewayAdminOnly allowed={canEdit}>
          <Component
            {...props}
            ref={ref}
            disabled={!canEdit || props.disabled}
            onChange={canEdit ? props.onChange : undefined}
            onClick={canEdit ? props.onClick : undefined}
            {...(Control === Chip
              ? { onDelete: canEdit ? props.onDelete : undefined }
              : {})}
          />
        </GatewayAdminOnly>
      );
    },
  );
  WriteControl.propTypes = /** @type {typeof WriteControl.propTypes} */ (
    /** @type {unknown} */ ({
      disabled: PropTypes.bool,
      onChange: PropTypes.func,
      onClick: PropTypes.func,
      onDelete: PropTypes.func,
    })
  );
  WriteControl.displayName = name;
  return /** @type {T} */ (/** @type {unknown} */ (WriteControl));
}

export const AdminButton = writeControl(Button, "AdminButton");
export const AdminIconButton = writeControl(IconButton, "AdminIconButton");
export const AdminTextField = writeControl(TextField, "AdminTextField");
export const AdminSwitch = writeControl(Switch, "AdminSwitch");
export const AdminSlider = writeControl(Slider, "AdminSlider");
export const AdminChip = writeControl(Chip, "AdminChip");
export const AdminToggleButtonGroup = writeControl(
  ToggleButtonGroup,
  "AdminToggleButtonGroup",
);
