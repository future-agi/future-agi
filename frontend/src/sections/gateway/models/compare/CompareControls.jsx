import React, { useContext } from "react";
import PropTypes from "prop-types";
import { Box, Button, Checkbox, Chip, Tooltip, Typography } from "@mui/material";
import { MAX_COMPARE_MODELS } from "./compareQuery";
import { ModelCompareContext, useModelCompare } from "./useModelCompare";
import ModelCompareDrawer from "./ModelCompareDrawer";

export function ModelCompareProvider({ data, children }) {
  const comparison = useModelCompare();
  return (
    <ModelCompareContext.Provider value={comparison}>
      {children}
      <ModelCompareDrawer data={data} />
    </ModelCompareContext.Provider>
  );
}
ModelCompareProvider.propTypes = { data: PropTypes.object.isRequired, children: PropTypes.node };

export function CompareModelCell({ data, children }) {
  const { ids, toggle } = useContext(ModelCompareContext);
  if (!data) return null;
  const checked = ids.includes(data.id);
  const full = !checked && ids.length >= MAX_COMPARE_MODELS;
  return (
    <Box sx={{ display: "flex", alignItems: "center", minWidth: 0, width: "100%", gap: 0.5 }}>
      <Tooltip title={full ? "Compare up to 3 models. Remove a selection first." : "Select for comparison"}>
        <Box component="span" data-model-compare sx={{ display: "inline-flex", flexShrink: 0 }}
          onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
          <Checkbox size="small" checked={checked} disabled={full}
            onChange={() => toggle(data.id)} inputProps={{ "aria-label": `Compare ${data.id}` }}
            sx={{ p: 0.5 }} />
        </Box>
      </Tooltip>
      <Box sx={{ minWidth: 0, flex: 1 }}>{children}</Box>
    </Box>
  );
}
CompareModelCell.propTypes = { data: PropTypes.object, children: PropTypes.node };

export function CompareToolbar({ rows }) {
  const { ids, toggle, clear, show } = useContext(ModelCompareContext);
  return (
    <Box role="group" aria-label="Model comparison"
      sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1 }}>
      <Button size="small" variant="outlined" disabled={ids.length < 2} onClick={show}>
        Compare ({ids.length}/{MAX_COMPARE_MODELS})
      </Button>
      {ids.map((id) => (
        <Chip key={id} size="small" label={rows.find((row) => row.id === id)?.name || id}
          aria-label={`Remove ${id} from comparison`} onDelete={() => toggle(id)}
          sx={{ maxWidth: 240 }} />
      ))}
      {ids.length > 0 && <Button size="small" onClick={clear}>Clear comparison</Button>}
      <Typography typography="s2" color="text.secondary">
        {ids.length === MAX_COMPARE_MODELS ? "3 selected. Remove one to replace it." : "Select 2–3 models using the row checkboxes."}
      </Typography>
    </Box>
  );
}
CompareToolbar.propTypes = { rows: PropTypes.array.isRequired };
