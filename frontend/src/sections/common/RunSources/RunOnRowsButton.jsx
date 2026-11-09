// `Run on these rows` from any door (R4-SPEC D24; lane Rb MUST 8). With a
// `datasetId` it opens the New experiment drawer there through
// runOnRowsTarget (> 50 rows go through the store prefill); without one it
// first asks `Run on which dataset?`. Frozen props (§I1).
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  ListSubheader,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { fmtCount } from "src/sections/common/format/fmt";
import { runOnRowsTarget } from "./runOnRows";
import { isSurfaceShown } from "src/launch/launchSwitch";
import DatasetOptionLabel from "src/sections/common/Datasets/DatasetOptionLabel";
import {
  pickerCaption,
  pickerOrder,
  pickerRows,
} from "src/sections/common/Datasets/pickerOrder";

/** `Support ops tasks · sandbox · 30 rows` (pure). */
export function datasetLine(d) {
  const n = Number(d?.row_count ?? d?.rowCount ?? d?.numberOfDatapoints);
  const rows = Number.isFinite(n)
    ? ` · ${fmtCount(n)} ${n === 1 ? "row" : "rows"}`
    : "";
  return `${d?.name ?? "Untitled dataset"}${rows}`;
}

/** Datasets matching `q` (name, case-insensitive), server order (recent first). */
export function matchDatasets(list, q) {
  const needle = String(q ?? "")
    .trim()
    .toLowerCase();
  const all = (list ?? []).filter((d) => d?.datasetId);
  return needle
    ? all.filter((d) =>
        String(d.name ?? "")
          .toLowerCase()
          .includes(needle),
      )
    : all;
}

function DatasetPickerDialog({ open, onClose, onPick }) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const { data, isLoading, isError } = useDevelopDatasetList(
    undefined,
    undefined,
    { enabled: open },
  );
  // W9 A4/A6: server order (starred first); once any dataset is in a
  // folder, the starred ones lead and the rest sit under folder headers.
  const list = useMemo(() => pickerOrder(matchDatasets(data, q)), [data, q]);
  const pick = (d) => d && onPick(d);
  // L5 G1: search takes the keys once the dialog has entered, so typing
  // right after "Save and continue" lands in it (autoFocus fires while the
  // previous dialog still holds focus).
  const searchRef = useRef(null);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="xs"
      aria-labelledby="run-on-which-dataset"
      TransitionProps={{ onEntered: () => searchRef.current?.focus() }}
    >
      <DialogTitle id="run-on-which-dataset" sx={{ pb: 1 }}>
        <Typography typography="m3" fontWeight="fontWeightSemiBold">
          Run on which dataset?
        </Typography>
        <Typography typography="s2" color="text.secondary">
          New experiment opens there with this preselected.
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ pt: 0.5 }}>
        <TextField
          autoFocus
          inputRef={searchRef}
          fullWidth
          size="small"
          value={q}
          placeholder="Search datasets"
          onChange={(e) => {
            setQ(e.target.value);
            setAt(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setAt((i) => Math.min(list.length - 1, i + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setAt((i) => Math.max(0, i - 1));
            } else if (e.key === "Enter") {
              e.preventDefault();
              pick(list[at]);
            }
          }}
          inputProps={{
            "aria-label": "Search datasets",
            "data-testid": "run-on-rows-search",
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="mdi:magnify"
                  width={18}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
        <Typography
          typography="s3"
          color="text.disabled"
          sx={{ mt: 1, mb: 0.25 }}
        >
          {`${pickerCaption(list)} · ↑ ↓ to move · Enter to pick`}
        </Typography>
        <List
          dense
          disablePadding
          sx={{ maxHeight: 320, overflowY: "auto" }}
          data-testid="run-on-rows-list"
        >
          {isLoading ? (
            <Typography typography="s2" color="text.secondary" sx={{ py: 1 }}>
              Loading datasets…
            </Typography>
          ) : isError ? (
            <Typography typography="s2" color="error.main" sx={{ py: 1 }}>
              Could not load datasets. Close and try again.
            </Typography>
          ) : list.length ? (
            pickerRows(list).map(
              ({ header, key, dataset: d, index: i, inStarred }) =>
                header !== undefined ? (
                  <ListSubheader
                    key={key}
                    disableSticky
                    disableGutters
                    data-testid="run-on-rows-folder"
                    sx={{
                      typography: "s3",
                      lineHeight: 2,
                      px: 1,
                      bgcolor: "transparent",
                      color: "text.secondary",
                    }}
                  >
                    {header}
                  </ListSubheader>
                ) : (
                  <ListItemButton
                    key={d.datasetId}
                    selected={i === at}
                    onMouseEnter={() => setAt(i)}
                    onClick={() => pick(d)}
                    data-testid="run-on-rows-dataset"
                    sx={{ borderRadius: 0.5 }}
                  >
                    <ListItemText
                      primary={
                        <DatasetOptionLabel
                          dataset={d}
                          suffix={datasetLine({ ...d, name: "" })}
                          showFolder={Boolean(inStarred)}
                        />
                      }
                      primaryTypographyProps={{
                        typography: "s1",
                        noWrap: true,
                      }}
                    />
                  </ListItemButton>
                ),
            )
          ) : (
            <Typography typography="s2" color="text.secondary" sx={{ py: 1 }}>
              {`No dataset matches "${q.trim()}".`}
            </Typography>
          )}
        </List>
      </DialogContent>
    </Dialog>
  );
}

DatasetPickerDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
};

// L6 launch switch: every door to New experiment exists only while
// experimentsBasic ships the drawer (a launch build has none by default).
// A door that preselects rows (`Re-test these rows`) also needs the rows
// choice dev cannot honour yet (exp.new.scope, data:rows:these): dev
// runs every row, so with experimentsBasic ON it is still absent.
export default function RunOnRowsButton(props) {
  const picksRows = (props.rowIds ?? []).filter(Boolean).length > 0;
  const shown =
    isSurfaceShown("exp.new") &&
    (!picksRows || isSurfaceShown("exp.new.scope")) &&
    (!props.split || isSurfaceShown("exp.new.splitScope"));
  return shown ? <RunOnRowsButtonInner {...props} /> : null;
}

function RunOnRowsButtonInner({
  datasetId,
  rowIds,
  split,
  source,
  promptId,
  promptVersionId,
  label,
  variant = "button",
  onDone,
  entry,
  query,
}) {
  const navigate = useNavigate();
  const [picking, setPicking] = useState(false);
  const toast = variant === "toastAction";
  // In a toolbar on a laptop screen the button is icon-only (label in the
  // tooltip), so it never squeezes the primary action next to it.
  const roomy = useMediaQuery("(min-width:1440px)");
  const iconOnly = variant === "button" && !roomy;

  const go = (id) => {
    const { href, prefill } = runOnRowsTarget({
      datasetId: id,
      rowIds,
      split,
      source,
      entry,
      promptId,
      promptVersionId,
    });
    if (prefill)
      useRunExperimentStore.getState().initiateCreateMode({ prefill });
    // Additive (options-truth L3 G5, L6): extra drawer params (`mode`,
    // `form`) of the door that opened the picker.
    const extra = query ? new URLSearchParams(query).toString() : "";
    navigate(extra ? `${href}&${extra}` : href);
    onDone?.();
  };
  const onClick = () => (datasetId ? go(datasetId) : setPicking(true));
  const n = (rowIds ?? []).filter(Boolean).length;
  const tip = datasetId
    ? n
      ? `Open New experiment on ${n === 1 ? "this row" : `these ${fmtCount(n)} rows`}`
      : "Open New experiment on this dataset"
    : "Pick a dataset, then New experiment opens there with this preselected";

  return (
    <>
      <CustomTooltip
        show={!toast}
        title={iconOnly ? `${label} · ${tip}` : tip}
        placement="top"
        size="small"
      >
        <Box
          component="span"
          data-testid="run-on-rows"
          sx={{ display: "inline-flex", flexShrink: 0 }}
        >
          {iconOnly ? (
            <IconButton size="small" aria-label={label} onClick={onClick}>
              <Iconify icon="mdi:play-circle-outline" width={18} />
            </IconButton>
          ) : (
            <Button
              size="small"
              variant={variant === "button" ? "outlined" : "text"}
              color={toast ? "inherit" : "primary"}
              onClick={onClick}
              startIcon={
                toast ? undefined : (
                  <Iconify icon="mdi:play-circle-outline" width={16} />
                )
              }
              sx={{
                typography: "s2",
                whiteSpace: "nowrap",
                ...(toast ? { fontWeight: "fontWeightMedium" } : null),
              }}
            >
              {label}
            </Button>
          )}
        </Box>
      </CustomTooltip>
      {datasetId ? null : (
        <DatasetPickerDialog
          open={picking}
          onClose={() => setPicking(false)}
          onPick={(d) => {
            setPicking(false);
            go(d.datasetId);
          }}
        />
      )}
    </>
  );
}

RunOnRowsButtonInner.propTypes = {
  datasetId: PropTypes.string,
  rowIds: PropTypes.arrayOf(PropTypes.string),
  split: PropTypes.string,
  source: PropTypes.string,
  promptId: PropTypes.string,
  promptVersionId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  label: PropTypes.string.isRequired,
  variant: PropTypes.oneOf(["button", "text", "toastAction"]),
  onDone: PropTypes.func,
  entry: PropTypes.string,
  query: PropTypes.object,
};

RunOnRowsButton.propTypes = RunOnRowsButtonInner.propTypes;
