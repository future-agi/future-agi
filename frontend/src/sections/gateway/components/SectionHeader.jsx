import React from "react";
import PropTypes from "prop-types";
import { Button } from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";

// Gateway pages share this wrapper; it renders the design-system PageHeader.
// `icon` is accepted for compatibility but no longer drawn (no icon tiles).
const SectionHeader = ({
  title,
  subtitle,
  actions,
  statusChip,
  children,
  sx,
}) => (
  <PageHeader
    title={title}
    badge={statusChip}
    description={subtitle}
    actions={
      actions || children ? (
        <>
          {actions?.map((action, idx) => (
            <Button
              key={action.label || idx}
              variant={action.variant || "outlined"}
              size={action.size || "small"}
              color={action.color || "primary"}
              startIcon={
                action.icon ? <Iconify icon={action.icon} width={16} /> : undefined
              }
              onClick={action.onClick}
              disabled={action.disabled}
            >
              {action.label}
            </Button>
          ))}
          {children}
        </>
      ) : undefined
    }
    sx={{ mb: 3, ...sx }}
  />
);

SectionHeader.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
  actions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      variant: PropTypes.string,
      size: PropTypes.string,
      color: PropTypes.string,
      icon: PropTypes.string,
      onClick: PropTypes.func,
      disabled: PropTypes.bool,
    }),
  ),
  statusChip: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
};

export default SectionHeader;
