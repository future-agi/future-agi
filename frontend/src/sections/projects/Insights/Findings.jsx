import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { CitedText } from "./Citation";
import { categoriesFor } from "./insightsDistribution";
import {
  citationBudget,
  normalizeFindings,
  verificationNote,
} from "./insightsFindings";
import { useReport } from "./reportContext";
import { CategoryLink, cautionColor } from "./parts";

const SEVERITY_LABEL = {
  high: "High severity",
  medium: "Medium severity",
  low: "Low severity",
};

const severityColor = (theme, severity) =>
  ({
    high: theme.palette.error.main,
    medium: cautionColor(theme),
    low: theme.palette.info.main,
  })[severity] || theme.palette.text.disabled;

function SeverityDot({ severity }) {
  const theme = useTheme();
  const dot = (
    <Box
      component="span"
      aria-label={SEVERITY_LABEL[severity]}
      role={severity ? "img" : undefined}
      sx={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        bgcolor: severityColor(theme, severity),
        flexShrink: 0,
      }}
    />
  );
  return severity ? (
    <Tooltip title={SEVERITY_LABEL[severity]} arrow placement="top">
      {dot}
    </Tooltip>
  ) : (
    dot
  );
}

SeverityDot.propTypes = { severity: PropTypes.string };

const MAX_CHIPS = 2;

function Finding({ finding, index }) {
  const report = useReport();
  const categories = categoriesFor(finding.categoryIds, report.categoriesById);
  // Past four citations, the rest fold into one "+N".
  const budget = citationBudget(finding, (n) => report.refIndex.has(n));
  const fold = (place) => ({
    hiddenRefs: budget.hidden,
    moreRefs:
      budget.at === place || (place === "detail" && budget.at === "trailing")
        ? budget.more
        : undefined,
  });
  return (
    <Box
      component="li"
      sx={{
        display: "grid",
        gridTemplateColumns: "20px minmax(0, 1fr) auto",
        columnGap: 1.25,
        py: 1.5,
        "&:not(:last-of-type)": {
          borderBottom: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums", lineHeight: "22px" }}
      >
        {index + 1}
      </Typography>
      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <Box sx={{ height: 22, display: "flex", alignItems: "center" }}>
            <SeverityDot severity={finding.severity} />
          </Box>
          <CitedText
            text={finding.headline}
            {...fold("headline")}
            component="p"
            sx={{
              m: 0,
              fontWeight: 600,
              fontSize: 14,
              lineHeight: "22px",
              color: "text.primary",
            }}
          />
        </Stack>
        {(finding.detail || finding.trailingRefs.length > 0) && (
          <CitedText
            text={finding.detail}
            extraRefs={finding.trailingRefs}
            {...fold("detail")}
            component="p"
            sx={{
              m: 0,
              mt: 0.5,
              pl: 2,
              fontSize: 13,
              lineHeight: "20px",
              color: "text.secondary",
            }}
          />
        )}
        {finding.action && (
          <Stack
            direction="row"
            spacing={0.75}
            alignItems="flex-start"
            sx={{ mt: 0.75, pl: 2 }}
          >
            <Iconify
              icon="mdi:arrow-right"
              width={14}
              sx={{ color: "primary.main", mt: "3px", flexShrink: 0 }}
            />
            <CitedText
              text={finding.action}
              {...fold("action")}
              component="p"
              sx={{
                m: 0,
                fontSize: 13,
                lineHeight: "20px",
                color: "text.primary",
              }}
            />
          </Stack>
        )}
      </Box>
      <Stack
        spacing={0.5}
        alignItems="flex-end"
        sx={{ pt: "1px", maxWidth: 220, minWidth: 0 }}
      >
        {categories.slice(0, MAX_CHIPS).map((category) => (
          <CategoryLink
            key={category.id}
            category={category}
            color={report.colorOf(category.id)}
            onClick={report.openCategory}
            maxWidth={220}
          />
        ))}
        {categories.length > MAX_CHIPS && (
          <Tooltip
            arrow
            title={categories
              .slice(MAX_CHIPS)
              .map((c) => c.name)
              .join(", ")}
          >
            <Typography variant="caption" color="text.secondary">
              +{categories.length - MAX_CHIPS} more
            </Typography>
          </Tooltip>
        )}
      </Stack>
    </Box>
  );
}

Finding.propTypes = {
  finding: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
};

/** The executive summary: numbered, scannable findings with their citations. */
export default function Findings({ summary }) {
  if (!summary || summary.error)
    return (
      <Typography variant="body2" color="text.secondary">
        The summary is unavailable for this run. The categories and statistics
        below are complete.
      </Typography>
    );
  const findings = normalizeFindings(summary);
  if (!findings.length)
    return (
      <Typography variant="body2" color="text.secondary">
        The summary found nothing it could state with numbers from this run. The
        categories below are complete.
      </Typography>
    );
  const note = verificationNote(summary, findings.length);
  return (
    <Box component="section" aria-label="Findings">
      <Box component="ol" sx={{ m: 0, p: 0, listStyle: "none" }}>
        {findings.map((finding, index) => (
          <Finding key={index} finding={finding} index={index} />
        ))}
      </Box>
      <Stack
        direction="row"
        spacing={0.75}
        alignItems="center"
        sx={{
          mt: 0.5,
          color: note.checked ? "text.secondary" : "warning.dark",
        }}
      >
        <Iconify
          icon={
            !note.checked
              ? "mdi:alert-outline"
              : note.verifier
                ? "mdi:shield-check-outline"
                : "mdi:check-circle-outline"
          }
          width={14}
          sx={{ color: note.checked ? "success.main" : "inherit" }}
        />
        <Typography variant="caption" color="inherit">
          {note.text}. Hover <b>#n</b> to preview an example.
        </Typography>
      </Stack>
    </Box>
  );
}

Findings.propTypes = { summary: PropTypes.object };
