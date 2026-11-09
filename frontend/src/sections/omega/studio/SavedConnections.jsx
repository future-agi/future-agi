import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import InputBase from "@mui/material/InputBase";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

import { serverDisplayName } from "./connect-link";
import StudioIcon from "./StudioIcon";
import { SAVED } from "./studio-copy";

const MONO =
  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace';

function LabelEditor({ initial, onSave, onCancel }) {
  const [label, setLabel] = useState(initial);
  return (
    <Box
      component="form"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(label);
      }}
      sx={{ display: "flex", alignItems: "center", gap: 0.5, minWidth: 0 }}
    >
      <InputBase
        autoFocus
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
        inputProps={{
          "aria-label": "Connection name",
          maxLength: 80,
          "data-testid": "studio-saved-label-input",
        }}
        sx={{
          flex: 1,
          minWidth: 0,
          px: 1,
          py: 0.25,
          typography: "s1",
          borderRadius: 0.75,
          border: "1px solid",
          borderColor: "primary.main",
        }}
      />
      <Button size="small" type="submit">
        {SAVED.save}
      </Button>
      <Button size="small" color="inherit" onClick={onCancel}>
        {SAVED.cancel}
      </Button>
    </Box>
  );
}

LabelEditor.propTypes = {
  initial: PropTypes.string.isRequired,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

function ConnectionRow({ item, onConnect, onRename, onRemove }) {
  const [editing, setEditing] = useState(false);
  const host = serverDisplayName(item.url);

  return (
    <Box
      data-testid="studio-saved-row"
      data-url={item.url}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 1,
        minHeight: 56,
        "&:not(:last-of-type)": {
          borderBottom: "1px solid",
          borderColor: "divider",
        },
        "&:hover .studio-row-tools": { opacity: 1 },
      }}
    >
      <Box
        sx={{
          width: 32,
          height: 32,
          borderRadius: 1,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "text.secondary",
          bgcolor: (theme) => alpha(theme.palette.grey[500], 0.08),
        }}
      >
        <StudioIcon name="terminal" size={16} />
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {editing ? (
          <LabelEditor
            initial={item.label}
            onCancel={() => setEditing(false)}
            onSave={(label) => {
              onRename(item.url, label);
              setEditing(false);
            }}
          />
        ) : (
          <>
            <Typography
              component="div"
              variant="s1"
              fontWeight="fontWeightMedium"
              color="text.primary"
              noWrap
              data-testid="studio-saved-label"
            >
              {item.label}
            </Typography>
            {item.label !== host && (
              <Typography
                component="div"
                variant="s2"
                color="text.secondary"
                noWrap
                sx={{ fontFamily: MONO }}
              >
                {host}
              </Typography>
            )}
          </>
        )}
      </Box>
      {!editing && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          <Box
            className="studio-row-tools"
            sx={{
              display: "flex",
              gap: 0.25,
              opacity: { xs: 1, md: 0.6 },
              transition: "opacity 120ms",
            }}
          >
            <Tooltip title={SAVED.rename}>
              <IconButton
                size="small"
                aria-label={`${SAVED.rename} ${item.label}`}
                onClick={() => setEditing(true)}
                sx={{ color: "text.secondary" }}
              >
                <StudioIcon name="pencil" size={15} />
              </IconButton>
            </Tooltip>
            <Tooltip title={SAVED.remove}>
              <IconButton
                size="small"
                aria-label={`${SAVED.remove} ${item.label}`}
                data-testid="studio-saved-remove"
                onClick={() => onRemove(item.url)}
                sx={{ color: "text.secondary" }}
              >
                <StudioIcon name="trash" size={15} />
              </IconButton>
            </Tooltip>
          </Box>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            data-testid="studio-saved-connect"
            onClick={() => onConnect(item.url)}
            sx={{ ml: 0.5, borderColor: "divider" }}
          >
            {SAVED.connect}
          </Button>
        </Box>
      )}
    </Box>
  );
}

ConnectionRow.propTypes = {
  item: PropTypes.shape({ url: PropTypes.string, label: PropTypes.string })
    .isRequired,
  onConnect: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

// Servers this browser connected to before: a label and an address each.
// Connecting from here sends no key. The memory-only child asks for a freshly
// opened/pasted link; the saved URL is not a saved credential.
export default function SavedConnections({
  items,
  onConnect,
  onRename,
  onRemove,
}) {
  if (!items.length) return null;
  return (
    <Box data-testid="studio-saved">
      <Typography
        component="h2"
        variant="s1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        sx={{ mb: 1 }}
      >
        {SAVED.title}
      </Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          bgcolor: "background.paper",
        }}
      >
        {items.map((item) => (
          <ConnectionRow
            key={item.url}
            item={item}
            onConnect={onConnect}
            onRename={onRename}
            onRemove={onRemove}
          />
        ))}
      </Box>
    </Box>
  );
}

SavedConnections.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({ url: PropTypes.string, label: PropTypes.string }),
  ).isRequired,
  onConnect: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};
