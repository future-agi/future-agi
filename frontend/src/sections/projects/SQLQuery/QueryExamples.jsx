import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from "@mui/material";
import { MetaChip } from "./queryUi";

// Vetted example queries from the catalog API. Each is ordinary SQL that the
// server compiles and scopes like any typed query; using one replaces the
// editor text (the page offers Undo).
export default function QueryExamples({
  examples,
  datasets,
  onUse,
  search = "",
}) {
  if (!examples?.length) return null;
  const unavailable = new Set(
    (datasets || [])
      .filter((dataset) => dataset.available === false)
      .map((dataset) => dataset.name),
  );
  const matches = examples.filter((example) =>
    `${example.title} ${example.description} ${example.datasets?.join(" ")} ${example.sql}`
      .toLowerCase()
      .includes(search.trim().toLowerCase()),
  );
  return (
    <Box sx={{ py: 1 }}>
      <Typography
        sx={{ px: 2, typography: "s1", fontWeight: "fontWeightSemiBold" }}
      >
        Example queries ({examples.length})
      </Typography>
      <Typography
        sx={{
          display: "block",
          px: 2,
          pb: 1,
          typography: "s3",
          color: "text.secondary",
        }}
      >
        Start with vetted SQL. Replaces the editor; Undo is available.
      </Typography>
      {!matches.length && (
        <Typography role="status" sx={{ p: 2, typography: "s2" }}>
          No matching examples. Try a dataset name.
        </Typography>
      )}
      <List dense disablePadding aria-label="Example queries">
        {matches.map((example) => {
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
              sx={{
                px: 2,
                py: 1,
                borderTop: 1,
                borderColor: "divider",
                "&.Mui-focusVisible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: -2,
                },
              }}
            >
              <ListItemText
                primary={example.title}
                secondary={
                  <>
                    <Box component="span" sx={{ display: "block", my: 0.5 }}>
                      {example.description}
                    </Box>
                    {missing.length ? (
                      <MetaChip
                        tone="warning"
                        icon="solar:danger-triangle-linear"
                      >
                        {`Needs ${missing.join(", ")} (unavailable)`}
                      </MetaChip>
                    ) : (
                      <Box
                        component="span"
                        sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}
                      >
                        {example.datasets?.map((name) => (
                          <MetaChip key={name} mono>
                            {name}
                          </MetaChip>
                        ))}
                      </Box>
                    )}
                  </>
                }
                primaryTypographyProps={{
                  sx: { typography: "s2", fontWeight: "fontWeightSemiBold" },
                }}
                secondaryTypographyProps={{
                  component: "div",
                  sx: { typography: "s2", color: "text.secondary" },
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
  search: PropTypes.string,
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
