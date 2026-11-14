import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { TEMPLATES } from "./insightsLenses";

const STEPS = [
  [
    "mdi:file-document-outline",
    "Reads a sample of your traces, sessions or users",
  ],
  ["mdi:shape-outline", "Groups them into categories and subcategories"],
  [
    "mdi:chart-box-outline",
    "Measures each one: how many, which users, how they end",
  ],
  ["mdi:format-quote-open", "Cites the conversations behind every finding"],
];

/** The right pane before the first report: what a report does, and the lenses. */
export default function InsightsEmpty({ onNew }) {
  return (
    <Box sx={{ px: 4, py: 5, maxWidth: 920 }}>
      <Typography variant="l3" component="h2" fontWeight={600}>
        What do you want to learn about your traffic?
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ mt: 1, maxWidth: 640 }}
      >
        Pick a question. Insights reads real conversations, finds the patterns
        and gives you numbers you can trace back to examples. A report takes a
        few minutes and can run again to show what changed.
      </Typography>

      <Box
        component="ol"
        sx={{
          m: 0,
          mt: 3,
          p: 0,
          listStyle: "none",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))",
          gap: 2,
        }}
      >
        {STEPS.map(([icon, text]) => (
          <Stack key={text} component="li" direction="row" spacing={1}>
            <Iconify
              icon={icon}
              width={16}
              sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
            />
            <Typography variant="caption" color="text.secondary">
              {text}
            </Typography>
          </Stack>
        ))}
      </Box>

      <Box
        sx={{
          mt: 4,
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))",
        }}
      >
        {TEMPLATES.map((template) => (
          <Box
            key={template.lens}
            component="button"
            type="button"
            onClick={() => onNew(template.lens)}
            sx={{
              p: 2.5,
              minHeight: 132,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
              textAlign: "left",
              font: "inherit",
              color: "text.primary",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              gap: 1,
              "&:hover": { borderColor: "text.disabled" },
              "&:hover .go": { color: "primary.main" },
              "&:focus-visible": {
                outline: "2px solid",
                outlineColor: "primary.main",
                outlineOffset: 2,
              },
            }}
          >
            <Box
              sx={{
                width: 32,
                height: 32,
                borderRadius: 1,
                bgcolor: "background.neutral",
                display: "grid",
                placeItems: "center",
              }}
            >
              <Iconify icon={template.icon} width={18} />
            </Box>
            <Typography variant="subtitle2" sx={{ fontSize: 15 }}>
              {template.title}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {template.description}
              {template.unit === "users" ? " Reads whole users." : ""}
            </Typography>
            <Stack
              className="go"
              direction="row"
              spacing={0.5}
              alignItems="center"
              sx={{ mt: "auto", color: "text.secondary" }}
            >
              <Typography variant="caption" fontWeight={600} color="inherit">
                Start
              </Typography>
              <Iconify icon="mdi:arrow-right" width={14} />
            </Stack>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

InsightsEmpty.propTypes = { onNew: PropTypes.func.isRequired };
