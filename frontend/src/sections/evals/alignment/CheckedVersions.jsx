import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import { useEvalVersions } from "src/sections/evals/hooks/useEvalVersions";
import { kappaText, pctText } from "./agreementModel";

const tabular = { fontVariantNumeric: "tabular-nums" };

/**
 * The eval's versions saved with a check: a tuned judge checked on held-out
 * rows before use (config_snapshot.checked_on, W7-2 B3) or an Improve-judge
 * draft checked on the rows it never saw (aligned_on, W7-6 B2). Renders
 * nothing without one.
 */
const CheckedVersions = ({ templateId }) => {
  // The page's own versions read (useEvalVersions: one query per template).
  const { data } = useEvalVersions(templateId);
  const checked = (data?.versions ?? []).filter(
    (v) => v.config_snapshot?.checked_on || v.config_snapshot?.aligned_on,
  );
  if (!checked.length) return null;
  return (
    <Stack gap={0.5} data-testid="w7-checked-versions">
      <Typography typography="s1" fontWeight={600}>
        Checked versions
      </Typography>
      {checked.map((v) => (
        <Typography
          key={v.id ?? v.version_number}
          typography="s3"
          sx={tabular}
          data-testid="w7-checked-version"
        >
          {lineOf(v)}
        </Typography>
      ))}
    </Stack>
  );
};

/**
 * One version's check: a tuned judge on held-out rows (checked_on), or an
 * Improve-judge draft on the rows it never saw (aligned_on).
 */
function lineOf(v) {
  const c = v.config_snapshot.checked_on;
  if (c)
    return `v${v.version_number} · judge ${v.model || c.model} · checked on ${c.n} ${c.split} rows: ${pctText(c.accuracy)} · κ ${kappaText(c.kappa)} (${c.vs?.model ?? "current judge"} ${pctText(c.vs?.accuracy)} · κ ${kappaText(c.vs?.kappa)})`;
  const a = v.config_snapshot.aligned_on;
  const unseen = a.split?.check_rows?.length ?? null;
  return `v${v.version_number} · improved from reviewers' labels · rows the draft never saw${unseen != null ? ` (${unseen})` : ""}: ${pctText(a.before?.check)} → ${pctText(a.after?.check)} · drafting rows: ${pctText(a.before?.draft)} → ${pctText(a.after?.draft)}`;
}

CheckedVersions.propTypes = { templateId: PropTypes.string };

export default CheckedVersions;
