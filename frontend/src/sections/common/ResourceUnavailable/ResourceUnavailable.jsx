import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import { Link } from "react-router-dom";

import EmptyContent from "src/components/empty-content";
import Iconify from "src/components/iconify";

import { RESOURCE_FORBIDDEN } from "./resourceUnavailableKind";

// Full-page state for a link to a record that was deleted, never existed, or
// belongs to another organisation or workspace. It replaces the page's
// loading shell, so the user is not left waiting on data that will not come.
export default function ResourceUnavailable({
  kind,
  resourceLabel,
  backTo,
  backLabel,
  sx,
}) {
  const forbidden = kind === RESOURCE_FORBIDDEN;
  const icon = forbidden
    ? "solar:lock-keyhole-linear"
    : "solar:file-search-linear";
  const title = forbidden
    ? `You don't have access to this ${resourceLabel}`
    : `This ${resourceLabel} doesn't exist or you don't have access`;
  const description = forbidden
    ? `Ask an admin of its workspace for access, or go back and choose another ${resourceLabel}.`
    : `It may have been deleted, or it belongs to an organisation or workspace you're not in. Check the link, or go back and choose another ${resourceLabel}.`;

  return (
    <EmptyContent
      role="alert"
      icon={icon}
      title={title}
      description={description}
      action={
        <Button
          component={Link}
          to={backTo}
          variant="outlined"
          color="inherit"
          size="small"
          startIcon={<Iconify icon="solar:arrow-left-linear" width={16} />}
        >
          {backLabel}
        </Button>
      }
      sx={{ minHeight: "60vh", backgroundColor: "background.paper", ...sx }}
    />
  );
}

ResourceUnavailable.propTypes = {
  kind: PropTypes.string,
  resourceLabel: PropTypes.string.isRequired,
  backTo: PropTypes.string.isRequired,
  backLabel: PropTypes.string.isRequired,
  sx: PropTypes.object,
};
