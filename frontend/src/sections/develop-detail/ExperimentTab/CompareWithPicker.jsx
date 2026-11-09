// IT-R1 G2 P9: the split half of a row's `Side by side` — `Compare with…`
// lists every other run (its own experiment first) and opens the two side
// by side. A plain menu; the hover actions own the main button.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Menu,
  MenuItem,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
// HO results R9 (W5 B-H5, door half): this run against a run on another
// dataset, rows matched by a key (W5 B7's one match dialog and endpoint),
// held by the tab's MatchDoorHost (a live grid re-creates this cell).
import { hasW5 } from "src/sections/common/Analysis/workbench/w5Api";
import {
  openMatchDoor,
  useMatchDoorReady,
} from "src/sections/common/Leaderboards/matchDoor";
import { pairRunText } from "./ComparePairPicker";
import { compareGroups } from "./attemptsModel";

export default function CompareWithPicker({ run, runs, partnerId, onPick }) {
  const [anchor, setAnchor] = useState(null);
  const hostReady = useMatchDoorReady();
  const canMatch = hostReady && hasW5("resultsMatch");
  const others = runs.filter(
    (r) => r.run_id !== run.run_id && r.listed !== false,
  );
  if (!others.length) return null;
  const rest = others.filter((r) => r.experiment_id !== run.experiment_id);
  const item = (r) => (
    <MenuItem
      key={r.run_id}
      selected={r.run_id === partnerId}
      onClick={() => {
        setAnchor(null);
        onPick(r);
      }}
      sx={{ typography: "s2" }}
    >
      {pairRunText(r)}
    </MenuItem>
  );
  return (
    <>
      <CustomTooltip show title="Compare with…" size="small" arrow>
        <IconButton
          size="small"
          aria-label={`Compare ${run.label} with another run`}
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ width: 22, height: 26, ml: -0.5, borderRadius: 0.5 }}
        >
          <Iconify icon="mdi:chevron-down" width={16} />
        </IconButton>
      </CustomTooltip>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { maxHeight: 360, minWidth: 260 } } }}
      >
        <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
          {`Compare ${run.label} with…`}
        </ListSubheader>
        {/* W1-1: a config with several runs lists them under its own
            heading, newest first (attemptsModel.compareGroups). */}
        {compareGroups(
          runs.filter(
            (r) => r.experiment_id === run.experiment_id && r.listed !== false,
          ),
        ).flatMap((g) => {
          const mine = g.runs.filter((r) => r.run_id !== run.run_id);
          if (!mine.length) return [];
          return g.runs.length > 1
            ? [
                <ListSubheader
                  key={`g-${g.key}`}
                  sx={{ typography: "s3", lineHeight: "28px", pl: 3 }}
                >
                  {g.title}
                </ListSubheader>,
                ...mine.map(item),
              ]
            : mine.map(item);
        })}
        {rest.length ? (
          <ListSubheader sx={{ typography: "s3", lineHeight: "28px" }}>
            Other experiments
          </ListSubheader>
        ) : null}
        {rest.map(item)}
        {canMatch && <Divider />}
        {canMatch && (
          <MenuItem
            onClick={() => {
              setAnchor(null);
              openMatchDoor({
                runs: runs.filter((r) => r.listed !== false),
                defaultRunId: run.run_id,
              });
            }}
            data-testid="compare-with-other-dataset"
          >
            <ListItemIcon>
              <Iconify icon="mdi:key-link" width={18} />
            </ListItemIcon>
            <ListItemText primaryTypographyProps={{ typography: "s2" }}>
              A run on another dataset…
            </ListItemText>
          </MenuItem>
        )}
      </Menu>
    </>
  );
}

CompareWithPicker.propTypes = {
  run: PropTypes.object.isRequired,
  runs: PropTypes.array.isRequired,
  partnerId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};
