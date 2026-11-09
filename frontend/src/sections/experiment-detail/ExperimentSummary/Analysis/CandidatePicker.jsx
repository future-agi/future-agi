import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { chipLabel, shortRunName } from "./verdictItems";

export const CANDIDATE_HELP =
  "The run you are judging against the baseline. Tasks to review, Review, the header strip and the Data tab follow it.";

/**
 * `Candidate #2D ●` (R3 analysis fix): the one run the page is about. Opens
 * a menu of the candidate runs in verdict order (best first) with each run's
 * verdict chip text; picking one writes `cand=`. `compact` (one view: the
 * Tasks to review header) reads `#2D ● ▾`. Every candidate is also in ⌘K
 * (`Candidate: #2C`).
 *
 * @param {object[]} props.items  `summaryVerdicts` items (best first)
 * @param {string} props.value  the candidate run id
 * @param {object} [props.colors]  `runColorMap` result
 * @param {(runId: string) => void} props.onChange
 * @param {boolean} [props.compact]
 */
export default function CandidatePicker({
  items,
  value,
  colors,
  onChange,
  compact = false,
}) {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const current = items.find((i) => i.run.run_id === value) ?? items[0];
  useRegisterCommands(
    "summary-candidate",
    items.map((i) => ({
      id: `summary.candidate.${i.run.run_id}`,
      group: "Actions",
      label: `Candidate: ${i.run.label}`,
      keywords: ["candidate", "judge", shortRunName(i.run.name) ?? ""],
      icon: "mdi:target",
      run: () => onChange?.(i.run.run_id),
      when: () => i.run.run_id !== current?.run.run_id,
    })),
    [items.map((i) => i.run.run_id).join(","), current?.run.run_id],
  );
  if (!current) return null;
  const best = items[0];
  const dot = (runId) => colors?.[runId]?.solid ?? "text.disabled";
  const choose = (runId) => {
    setOpen(false);
    if (runId !== current.run.run_id) onChange?.(runId);
  };

  return (
    <>
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        describeChild
        title={`${current.run.label} ${shortRunName(current.run.name)} · ${CANDIDATE_HELP}`}
      >
        <Button
          ref={anchorRef}
          variant="outlined"
          size="small"
          onClick={() => setOpen(true)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          aria-haspopup="menu"
          aria-label={`Candidate ${current.run.label}. Change candidate`}
          data-testid="candidate-picker"
          sx={{
            px: compact ? 0.75 : 1.25,
            height: compact ? 28 : 32,
            gap: compact ? 0.5 : 0.75,
            minWidth: 0,
            flexShrink: 0,
            whiteSpace: "nowrap",
            ...(compact && {
              borderColor: "divider",
              "& .MuiButton-endIcon": { ml: 0 },
            }),
          }}
        >
          {compact ? null : (
            <Typography typography="s1" color="text.secondary" noWrap>
              Candidate
            </Typography>
          )}
          <Typography
            typography={compact ? "s2" : "s1"}
            fontWeight="fontWeightMedium"
            color="text.primary"
            noWrap
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {current.run.label}
          </Typography>
          <Box
            component="span"
            aria-hidden
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              flexShrink: 0,
              bgcolor: dot(current.run.run_id),
            }}
          />
        </Button>
      </CustomTooltip>
      <Menu
        anchorEl={anchorRef.current}
        open={open}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 340 } } }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 2, pt: 0.5, pb: 1, whiteSpace: "normal" }}
        >
          Compare one run with baseline {current.base.label}. Best verdict
          first.
        </Typography>
        {items.map((i) => (
          <MenuItem
            key={i.run.run_id}
            selected={i.run.run_id === current.run.run_id}
            onClick={() => choose(i.run.run_id)}
          >
            <Box
              component="span"
              aria-hidden
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                mr: 1.25,
                flexShrink: 0,
                bgcolor: dot(i.run.run_id),
              }}
            />
            <ListItemText
              primary={
                <Stack direction="row" gap={0.75} alignItems="baseline">
                  <Typography typography="s1" fontWeight="fontWeightMedium">
                    {i.run.label}
                  </Typography>
                  <Typography typography="s1" color="text.secondary" noWrap>
                    {shortRunName(i.run.name)}
                  </Typography>
                </Stack>
              }
              secondary={`${chipLabel(i.verdict)}${i === best ? " · best verdict" : ""}`}
              secondaryTypographyProps={{ typography: "s3" }}
            />
            {i.run.run_id === current.run.run_id ? (
              <Iconify
                icon="mdi:check"
                width={16}
                sx={{ color: "primary.main", ml: 1 }}
              />
            ) : null}
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

CandidatePicker.propTypes = {
  items: PropTypes.array.isRequired,
  value: PropTypes.string,
  colors: PropTypes.object,
  onChange: PropTypes.func,
  compact: PropTypes.bool,
};
