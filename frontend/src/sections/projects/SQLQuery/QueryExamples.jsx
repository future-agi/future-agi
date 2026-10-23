import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from "@mui/material";

// Vetted example queries from the catalog API. Each is ordinary SQL that the
// server compiles and scopes like any typed query; using one replaces the
// editor text (the page offers Undo).
export default function QueryExamples({ examples, datasets, onUse }) {
  if (!examples?.length) return null;
  const unavailable = new Set(
    (datasets || [])
      .filter((dataset) => dataset.available === false)
      .map((dataset) => dataset.name),
  );
  return (
    <Box component="details" sx={{ px: 2, py: 0.5 }}>
      <Typography
        component="summary"
        sx={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}
      >
        Example queries ({examples.length})
      </Typography>
      <List dense disablePadding aria-label="Example queries">
        {examples.map((example) => {
          const missing = (example.datasets || []).filter((name) =>
            unavailable.has(name),
          );
          return (
            <ListItemButton
              key={example.id}
              disabled={missing.length > 0}
              // Disabled styling only blocks pointer events; never apply an
              // example whose dataset is unavailable.
              onClick={() => !missing.length && onUse(example)}
              aria-label={`Use example: ${example.title}`}
              title={example.description}
              sx={{ px: 1, py: 0.25 }}
            >
              <ListItemText
                primary={example.title}
                secondary={
                  missing.length
                    ? `Needs ${missing.join(", ")} (unavailable)`
                    : example.datasets.join(" · ")
                }
                primaryTypographyProps={{ fontSize: 12 }}
                secondaryTypographyProps={{
                  fontSize: 10,
                  fontFamily: "monospace",
                }}
              />
            </ListItemButton>
          );
        })}
      </List>
    </Box>
  );
}

QueryExamples.propTypes = {
  examples: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      description: PropTypes.string,
      sql: PropTypes.string.isRequired,
      datasets: PropTypes.arrayOf(PropTypes.string),
    }),
  ),
  datasets: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      available: PropTypes.bool,
    }),
  ),
  onUse: PropTypes.func.isRequired,
};
