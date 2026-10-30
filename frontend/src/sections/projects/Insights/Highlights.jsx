import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { CitationChip, EvidenceQuote } from "./Citation";
import { highlightTitle } from "./insightsFindings";
import { useReport } from "./reportContext";
import { Swatch } from "./parts";

function HighlightCard({ highlight }) {
  const report = useReport();
  const category = report.categoriesById.get(highlight.category_id);
  const quote =
    highlight.quote || report.previews.get(highlight.unit_id)?.quote || "";
  const title = highlightTitle(highlight);
  return (
    <Box
      sx={{
        p: 1.75,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        minWidth: 0,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Typography
          variant="subtitle2"
          title={highlight.summary}
          sx={{
            flex: 1,
            minWidth: 0,
            lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {title}
        </Typography>
        <CitationChip n={highlight.ref} unitId={highlight.unit_id} />
      </Stack>
      {highlight.reason && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontSize: 13, lineHeight: "19px" }}
        >
          {highlight.reason}
        </Typography>
      )}
      <EvidenceQuote quote={quote} dense />
      {category && (
        <Stack direction="row" spacing={0.5} alignItems="center">
          <Swatch color={report.colorOf(category.id)} size={8} />
          <Typography
            variant="caption"
            color="text.secondary"
            noWrap
            title={category.name}
          >
            {category.name}
          </Typography>
        </Stack>
      )}
      <Stack
        direction="row"
        spacing={1}
        justifyContent="flex-end"
        sx={{ mt: "auto", pt: 0.25 }}
      >
        {category && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => report.openCategory(category.id)}
            sx={{
              fontSize: 12,
              py: 0.25,
              px: 1,
              whiteSpace: "nowrap",
              flexShrink: 0,
            }}
          >
            View category
          </Button>
        )}
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => report.openExample(highlight.unit_id)}
          sx={{
            fontSize: 12,
            py: 0.25,
            px: 1,
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          Open example
        </Button>
      </Stack>
    </Box>
  );
}

HighlightCard.propTypes = { highlight: PropTypes.object.isRequired };

/** Exemplar conversations the summary points to, as a collapsible grid. */
export default function Highlights({ highlights }) {
  const [open, setOpen] = useState(true);
  if (!highlights?.length) return null;
  return (
    <Box component="section" aria-label="Highlighted examples">
      <Button
        color="inherit"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        endIcon={
          <Iconify
            icon={open ? "mdi:chevron-up" : "mdi:chevron-down"}
            width={16}
          />
        }
        sx={{
          px: 0.75,
          ml: -0.75,
          fontSize: 13,
          fontWeight: 500,
          color: "primary.main",
        }}
      >
        {open
          ? "Hide highlighted examples"
          : `Show ${highlights.length} highlighted examples`}
      </Button>
      <Collapse in={open}>
        <Box
          sx={{
            mt: 1,
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          }}
        >
          {highlights.map((highlight) => (
            <HighlightCard
              key={`${highlight.ref}-${highlight.unit_id}`}
              highlight={highlight}
            />
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

Highlights.propTypes = { highlights: PropTypes.array };
