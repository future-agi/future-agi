import PropTypes from "prop-types";
import React from "react";
import SideDrawer from "src/sections/simulate-v2/components/SideDrawer";
import PersonaInfo from "./PersonaInfo";

/* A persona's details, with what you can do with it — the actions are
   offered only where the page that opened it supports them. */
const PersonaInfoDrawer = ({
  open,
  onClose,
  persona,
  usage,
  onUse,
  onDuplicate,
  onEdit,
}) => (
  <SideDrawer open={open} onClose={onClose} width={640}>
    <PersonaInfo
      persona={persona}
      usage={usage}
      onUse={onUse}
      onDuplicate={onDuplicate}
      onEdit={onEdit}
      onClose={onClose}
    />
  </SideDrawer>
);

PersonaInfoDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  persona: PropTypes.object,
  usage: PropTypes.object,
  onUse: PropTypes.func,
  onDuplicate: PropTypes.func,
  onEdit: PropTypes.func,
};

export default PersonaInfoDrawer;
