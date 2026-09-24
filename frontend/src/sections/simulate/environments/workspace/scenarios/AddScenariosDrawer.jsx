import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Checkbox, InputBase, Divider,
} from "@mui/material";

import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import EmptyState from "../../components/EmptyState";
import { ADD_COPY, addCandidates } from "./addScenarios.constants";
import { ENV_SHAPE, SCENARIO_SHAPE } from "./scenarios.shapes";

const checkboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// Add scenarios.
//
// Scenarios already exist by the time anyone reaches this drawer — they are
// derived when the environment is built. So this is not how you get scenarios,
// it is how you add ones the derivation could not know to write. The single
// route generates from the agent (mock, no backend): pick from the pool of
// probes not already on the environment and they append through `onAdd`.
export default function AddScenariosDrawer({ open, onClose, env, selected, onAdd }) {
  const candidates = useMemo(() => addCandidates(env, selected), [env, selected]);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(() => new Set());

  const q = query.trim().toLowerCase();
  const shown = candidates.filter((r) => {
    if (!q) return true;
    const hay = `${r.name || ""} ${r.summary || ""} ${r.useCase || ""} ${r.persona?.name || ""}`.toLowerCase();
    return hay.includes(q);
  });
  const shownIds = shown.map((r) => r.id);
  const allShownPicked = shownIds.length > 0 && shownIds.every((id) => picked.has(id));
  const someShownPicked = shownIds.some((id) => picked.has(id)) && !allShownPicked;

  const close = () => { setPicked(new Set()); setQuery(""); onClose(); };
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setPicked((prev) => {
    if (allShownPicked) {
      const next = new Set(prev);
      shownIds.forEach((id) => next.delete(id));
      return next;
    }
    return new Set([...prev, ...shownIds]);
  });

  const confirm = () => {
    const rows = candidates.filter((r) => picked.has(r.id));
    if (rows.length) onAdd(rows);
    close();
  };

  return (
    <SideDrawer open={open} onClose={close} width={720}>
      <Stack sx={{ height: "100%" }}>
        <Box sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Typography sx={{ typography: "m2", fontWeight: "fontWeightSemiBold" }}>
            {ADD_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
            {ADD_COPY.subtitle}
          </Typography>
        </Box>

        {candidates.length === 0 ? (
          <Box sx={{ flex: 1, display: "grid", placeItems: "center", p: 4 }}>
            <EmptyState
              icon="solar:check-circle-linear"
              title={ADD_COPY.emptyTitle}
              body={ADD_COPY.emptyBody}
            />
          </Box>
        ) : (
          <>
            <Stack
              direction="row" alignItems="center" spacing={1}
              sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
            >
              <Iconify icon="solar:magnifer-linear" width={16} sx={{ color: "text.subtitle" }} />
              <InputBase
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={ADD_COPY.searchPlaceholder}
                sx={{ typography: "s2", flex: 1 }}
              />
            </Stack>

            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ px: 2.5, py: 1, bgcolor: "background.neutral", borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
            >
              <Checkbox
                size="small"
                checked={allShownPicked}
                indeterminate={someShownPicked}
                onChange={toggleAll}
                inputProps={{ "aria-label": ADD_COPY.selectAll }}
                sx={checkboxSx}
              />
              <Typography sx={{ typography: "s3", color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, fontWeight: "fontWeightBold" }}>
                {ADD_COPY.routeLabel} · {shown.length}
              </Typography>
            </Stack>

            <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
              <Stack divider={<Divider />}>
                {shown.map((r) => (
                  <CandidateRow key={r.id} row={r} checked={picked.has(r.id)} onToggle={() => toggle(r.id)} />
                ))}
              </Stack>
            </Box>
          </>
        )}

        <Stack
          direction="row" justifyContent="flex-end" spacing={1}
          sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Button onClick={close} sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", color: "text.secondary" }}>
            {ADD_COPY.cancel}
          </Button>
          <Button
            variant="contained" color="primary" size="small"
            disabled={picked.size === 0}
            onClick={confirm}
            sx={{ typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {ADD_COPY.confirm(picked.size)}
          </Button>
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

AddScenariosDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: ENV_SHAPE,
  selected: PropTypes.arrayOf(SCENARIO_SHAPE),
  onAdd: PropTypes.func,
};

function CandidateRow({ row, checked, onToggle }) {
  const p = row.persona;
  return (
    <Stack
      direction="row" alignItems="flex-start" spacing={1.5}
      onClick={onToggle}
      sx={{
        px: 2.5, py: 1.5, cursor: "pointer",
        bgcolor: checked ? (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03) : "transparent",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Checkbox
        size="small"
        checked={checked}
        onChange={onToggle}
        onClick={(e) => e.stopPropagation()}
        inputProps={{ "aria-label": row.name }}
        sx={{ ...checkboxSx, mt: 0.25 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>
          {row.name}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {row.useCase}
        </Typography>
        {p?.name && (
          <Typography sx={{ typography: "s3", color: "text.disabled", mt: 0.25 }}>
            {p.name}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
CandidateRow.propTypes = {
  row: SCENARIO_SHAPE,
  checked: PropTypes.bool,
  onToggle: PropTypes.func,
};
