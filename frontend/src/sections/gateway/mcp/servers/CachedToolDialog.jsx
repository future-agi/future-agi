import React, { useId } from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";
import JsonBlock from "../components/JsonBlock";
import RuntimeNotice from "../runtimeNotice";

// Local inspection only: never fetch metadata or synthesize missing schemas.
// NEW RL-1009 labels: Annotations / Input schema; claims review remains owed.
export default function CachedToolDialog({ tool, onClose }) {
  const titleId = useId();
  const schema = tool?.inputSchema ?? tool?.input_schema;
  return (
    <Dialog
      open={Boolean(tool)}
      onClose={onClose}
      aria-labelledby={titleId}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle id={titleId}>{tool?.name}</DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
      >
        <RuntimeNotice />
        {tool?.description && (
          <Typography typography="s2">{tool.description}</Typography>
        )}
        {tool?.annotations != null && (
          <>
            <Typography component="h3" typography="s1">
              Annotations
            </Typography>
            <JsonBlock
              value={tool.annotations}
              label="Cached tool annotations"
            />
          </>
        )}
        {schema != null && (
          <>
            <Typography component="h3" typography="s1">
              Input schema
            </Typography>
            <JsonBlock value={schema} label="Cached tool input schema" />
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
CachedToolDialog.propTypes = {
  tool: PropTypes.object,
  onClose: PropTypes.func.isRequired,
};
