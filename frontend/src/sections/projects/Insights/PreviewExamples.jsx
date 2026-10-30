import React from "react";
import PropTypes from "prop-types";
import { Box, Paper, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { EvidenceQuote } from "./Citation";
import { AttributePills, FacetChips } from "./parts";

// Why the report would leave an example out, in the user's terms.
function leftOutReason(unit, lens, attributes) {
  const facets = unit.facets || {};
  const filter = attributes.find(
    (a) => a.filter_by && a.type === "boolean" && !facets.attributes?.[a.key],
  );
  if (filter) return `“${filter.key}” is false`;
  // A failure the reading named but nothing it could quote shows.
  if (lens === "failures" && facets.unbacked_failure)
    return "Named a failure the conversation doesn't show";
  if (lens === "failures" && !facets.failure_present)
    return "No agent failure found";
  return "Not relevant to the question";
}

export default function PreviewExamples({ units, lens, attributes = [] }) {
  return (
    <Box
      sx={{
        display: "grid",
        gap: 1.5,
        gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
      }}
    >
      {units.map((unit) => {
        const facets = unit.facets || {};
        const included = unit.included !== false;
        return (
          <Paper
            key={unit.unit_id}
            variant="outlined"
            sx={{
              p: 1.75,
              borderRadius: 1.5,
              opacity: included ? 1 : 0.8,
              display: "flex",
              flexDirection: "column",
              gap: 1,
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
            >
              <Stack
                direction="row"
                spacing={0.5}
                alignItems="center"
                sx={{ color: included ? "success.dark" : "text.secondary" }}
              >
                <Iconify
                  icon={
                    included
                      ? "mdi:check-circle-outline"
                      : "mdi:minus-circle-outline"
                  }
                  width={16}
                />
                <Typography variant="caption" fontWeight={600}>
                  {included ? "Included" : "Would be left out"}
                </Typography>
              </Stack>
              {unit.user_id && (
                <Typography variant="caption" color="text.secondary" noWrap>
                  {unit.user_id}
                </Typography>
              )}
            </Stack>
            {!included && (
              <Typography variant="caption" color="text.secondary">
                {leftOutReason(unit, lens, attributes)}
              </Typography>
            )}
            <Typography variant="subtitle2" noWrap title={unit.label}>
              {unit.label || unit.excerpt || unit.unit_id}
            </Typography>
            {facets.summary && (
              <Typography variant="body2">{facets.summary}</Typography>
            )}
            {facets.lens_answer && (
              <Typography variant="body2" color="text.secondary">
                <Box component="span" sx={{ fontWeight: 600 }}>
                  Answer:{" "}
                </Box>
                {facets.lens_answer}
              </Typography>
            )}
            {(facets.evidence || [])
              .filter((e) => e?.quote)
              .slice(0, 2)
              .map((e) => (
                <EvidenceQuote key={e.quote} quote={e.quote} dense />
              ))}
            <FacetChips facets={facets} dense />
            <AttributePills attributes={facets.attributes} />
          </Paper>
        );
      })}
    </Box>
  );
}

PreviewExamples.propTypes = {
  units: PropTypes.array.isRequired,
  lens: PropTypes.string,
  attributes: PropTypes.array,
};
