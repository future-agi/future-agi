// IT-R1 G2 P2: one side of the Experiments headline's
// `Compare [#2G Support Agent v3.1 (newest) ▾] with [#2B Support Agent v3
// (previous) ▾]`. A single-select, searchable list of every run on the
// dataset, grouped by experiment, built from the shared RunPickerRow.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Popover, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { runMatches } from "src/sections/common/Runs/runMatch";
import {
  PickerGroupTitle,
  RunPickerRow,
} from "src/sections/common/Runs/RunPickerList";
import { agentShortName } from "src/sections/experiment-detail/matrix/matrixModel";

/** `#2G Support Agent v3.1 (newest)`: id + the one short name + its role. */
export function pairRunText(run, role) {
  if (!run) return "";
  return [run.label, agentShortName(run), role ? `(${role})` : null]
    .filter(Boolean)
    .join(" ");
}

const groupOf = (run) =>
  String(run?.label ?? "").match(/^#(\d+)/)?.[1] ?? "other";

/**
 * @param {object} props
 * @param {object[]} props.runs  every listed run (+ the baseline)
 * @param {string|null} props.value  the picked run id
 * @param {string|null} [props.otherId]  the other side (not offered)
 * @param {Object<string, string>} [props.roles]  run id → `newest` / …
 * @param {(runId: string) => void} props.onChange
 * @param {string} props.ariaLabel  `Run to compare` / `Compare with`
 */
export default function ComparePairPicker({
  runs,
  value,
  otherId,
  roles = {},
  onChange,
  ariaLabel,
}) {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const current = runs.find((r) => r.run_id === value) ?? null;
  const groups = useMemo(() => {
    const offered = runs.filter(
      (r) => r.run_id !== otherId && runMatches(r, search),
    );
    const m = new Map();
    offered.forEach((r) => {
      const g = groupOf(r);
      m.set(g, [...(m.get(g) ?? []), r]);
    });
    return [...m.entries()].sort((a, b) => Number(b[0]) - Number(a[0]));
  }, [runs, otherId, search]);
  const close = () => {
    setOpen(false);
    setSearch("");
  };
  return (
    <>
      <Button
        ref={anchorRef}
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => setOpen(true)}
        aria-label={`${ariaLabel}: ${pairRunText(current, roles[value])}`}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        data-testid="compare-pair-picker"
        sx={{
          height: 28,
          px: 1,
          minWidth: 0,
          maxWidth: 340,
          typography: "s2",
          fontWeight: 500,
          borderColor: "divider",
          justifyContent: "space-between",
        }}
      >
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            minWidth: 0,
          }}
        >
          {current ? pairRunText(current, roles[value]) : "Pick a run"}
        </Box>
      </Button>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { width: 380, maxHeight: 420, p: 1, mt: 0.5 } },
        }}
      >
        <Stack gap={0.75}>
          <FormSearchField
            autoFocus
            size="small"
            placeholder="Search runs, models, agents"
            searchQuery={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Box sx={{ overflowY: "auto", maxHeight: 340 }}>
            {groups.length === 0 ? (
              <Typography typography="s2" color="text.secondary" sx={{ p: 1 }}>
                No runs match the search
              </Typography>
            ) : (
              groups.map(([g, list]) => (
                <Box key={g} sx={{ mb: 0.5 }}>
                  <PickerGroupTitle>
                    {g === "other" ? "Other runs" : `Experiment #${g}`}
                  </PickerGroupTitle>
                  {list.map((r) => (
                    <RunPickerRow
                      key={r.run_id}
                      run={r}
                      selected={r.run_id === value}
                      isPinned={!!r.is_dataset_baseline}
                      versionTag={
                        roles[r.run_id]
                          ? { text: roles[r.run_id], role: null }
                          : null
                      }
                      onToggle={(run) => {
                        onChange(run.run_id);
                        close();
                      }}
                    />
                  ))}
                </Box>
              ))
            )}
          </Box>
        </Stack>
      </Popover>
    </>
  );
}

ComparePairPicker.propTypes = {
  runs: PropTypes.array.isRequired,
  value: PropTypes.string,
  otherId: PropTypes.string,
  roles: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  ariaLabel: PropTypes.string.isRequired,
};
