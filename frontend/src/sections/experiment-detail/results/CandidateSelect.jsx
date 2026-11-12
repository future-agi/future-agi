// `Candidate #2G ▾` beside Baseline (8a): the one run the page
// is about. Each row: label, name, version chip (`v3.1 (draft) · Latest
// version`). Picking writes `cand=` (the header, What changed, verdicts,
// worklist, Review and the Summary follow it).
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runShortName } from "src/sections/common/Runs/runLabel";
// HO results R9 (W5 B-H5, door half): the candidate against a run on another
// dataset, rows matched by a key (W5 B7's one match dialog and endpoint).
import MatchByKeyDialog from "src/sections/common/CompareRuns/MatchByKeyDialog";
import { hasW5 } from "src/sections/common/Analysis/workbench/w5Api";

export const CANDIDATE_TIP =
  "The run you are judging against the baseline. What changed, the verdicts, Tasks to review, Review and Choose winner follow it.";

export function VersionChip({ tag }) {
  if (!tag) return null;
  return (
    <Chip
      size="small"
      variant="outlined"
      label={tag.role ? `${tag.text} · ${tag.role}` : tag.text}
      sx={{ height: 18, typography: "s3", "& .MuiChip-label": { px: 0.5 } }}
    />
  );
}
VersionChip.propTypes = { tag: PropTypes.object };

export default function CandidateSelect({
  runs,
  value,
  baselineId,
  colors,
  tags,
  onChange,
}) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [matchOpen, setMatchOpen] = useState(false);
  const canMatch = hasW5("resultsMatch");
  const options = (runs ?? []).filter((r) => r.run_id !== baselineId);
  const current = options.find((r) => r.run_id === value) ?? null;
  if (!current) return null;
  const dot = (id) => colors?.[id]?.solid ?? "text.disabled";
  return (
    <>
      <CustomTooltip show size="small" placement="bottom" title={CANDIDATE_TIP}>
        <Button
          ref={ref}
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => setOpen(true)}
          aria-haspopup="menu"
          aria-label={`Candidate ${current.label}. Change candidate`}
          data-testid="candidate-select"
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{
            height: 30,
            typography: "s2",
            borderColor: "divider",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }}
        >
          <Box component="span" sx={{ color: "text.secondary", mr: 0.5 }}>
            Candidate
          </Box>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {current.label}
          </Box>
          <Box
            component="span"
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: dot(current.run_id),
              ml: 0.75,
            }}
          />
        </Button>
      </CustomTooltip>
      <Menu
        anchorEl={ref.current}
        open={open}
        onClose={() => setOpen(false)}
        slotProps={{ paper: { sx: { minWidth: 320 } } }}
      >
        {options.map((r) => (
          <MenuItem
            key={r.run_id}
            selected={r.run_id === value}
            onClick={() => {
              setOpen(false);
              if (r.run_id !== value) onChange(r.run_id);
            }}
            sx={{ gap: 1 }}
          >
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                bgcolor: dot(r.run_id),
                flexShrink: 0,
              }}
            />
            <ListItemText
              primary={
                <Typography typography="s2" noWrap>
                  <Box component="span" sx={{ fontWeight: 600, mr: 0.75 }}>
                    {r.label}
                  </Box>
                  {runShortName(r)}
                </Typography>
              }
            />
            <VersionChip tag={tags?.[r.run_id]} />
          </MenuItem>
        ))}
        {canMatch && <Divider />}
        {canMatch && (
          <MenuItem
            onClick={() => {
              setOpen(false);
              setMatchOpen(true);
            }}
            data-testid="candidate-match-other-dataset"
          >
            <ListItemIcon>
              <Iconify icon="mdi:key-link" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s2" }}>
              {`Compare ${current.label} with a run on another dataset…`}
            </ListItemText>
          </MenuItem>
        )}
      </Menu>
      {matchOpen && (
        <MatchByKeyDialog
          open
          onClose={() => setMatchOpen(false)}
          runs={runs ?? []}
          defaultRunId={current.run_id}
        />
      )}
    </>
  );
}

CandidateSelect.propTypes = {
  runs: PropTypes.array,
  value: PropTypes.string,
  baselineId: PropTypes.string,
  colors: PropTypes.object,
  tags: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
