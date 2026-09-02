import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Popover, Menu, MenuItem, Switch,
  TextField, InputAdornment, Checkbox, Divider, Tooltip, Tab,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SegmentedTabs } from "src/components/tabs/tabs";
import { FilterPanel } from "src/components/filter-panel";
import {
  VIEWS, ROW_HEIGHTS, GROUPINGS, QUICK_FILTERS, SORTS,
  filterFields, activeFilterCount,
} from "../_mock/compareView";

/**
 * The controls above a comparison.
 *
 * Four things, in the order someone reaches for them: find a scenario, narrow
 * to the ones that matter, change how they are drawn, act on what you found.
 * Filter and Display are deliberately different: Filter decides which rows
 * exist, Display decides what a row looks like. Mixing them produces a panel
 * where unticking something sometimes hides data and sometimes hides a column,
 * and nobody can predict which.
 */
/**
 * Saved views.
 *
 * The same idea as Observe's view tabs, in the space a comparison screen has:
 * a switcher that names the view you are in, lists the saved ones, and offers
 * to save the current reading when it differs from them. Dirty state is what
 * makes it usable — without it you cannot tell whether what you are looking at
 * is the saved view or something you have since changed.
 */
export function SavedViews({
  views, activeId, dirty, onApply, onSave, onUpdate, onRename, onDelete,
}) {
  const buttonRef = useRef(null);
  const [anchor, setAnchor] = useState(null);
  const [naming, setNaming] = useState(null); // { mode: "save" | "rename", value, id }
  const active = views.find((v) => v.id === activeId);

  const commit = () => {
    const name = (naming.value || "").trim();
    if (!name) return;
    if (naming.mode === "save") onSave(name);
    else onRename(naming.id, name);
    setNaming(null);
  };

  return (
    <>
      <Button
        size="small"
        ref={buttonRef}
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:bookmark-linear" width={15} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={13} />}
        sx={{
          typography: "s2", fontWeight: 600, color: "text.secondary",
          border: "1px solid", borderColor: "divider", maxWidth: 240,
        }}
      >
        <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {active ? active.name : "All scenarios"}
        </Box>
        {dirty && (
          <Box
            component="span"
            sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main", ml: 0.75, flexShrink: 0 }}
          />
        )}
      </Button>

      <Menu
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setNaming(null); }}
        slotProps={{ paper: { sx: { width: 300 } } }}
      >
        <Typography
          sx={{ px: 2, pt: 1, pb: 0.5, typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}
        >
          Views
        </Typography>

        <MenuItem
          onClick={() => { onApply(null); setAnchor(null); }}
          sx={{ typography: "s2", py: 0.875 }}
        >
          <Iconify
            icon={activeId ? "solar:list-linear" : "solar:check-circle-bold"}
            width={16}
            sx={{ mr: 1.25, color: activeId ? "text.subtitle" : "primary.main" }}
          />
          All scenarios
        </MenuItem>

        {views.map((v) => (
          <MenuItem
            key={v.id}
            onClick={() => { onApply(v.id); setAnchor(null); }}
            sx={{ typography: "s2", py: 0.875 }}
          >
            <Iconify
              icon={v.id === activeId ? "solar:check-circle-bold" : "solar:bookmark-linear"}
              width={16}
              sx={{ mr: 1.25, color: v.id === activeId ? "primary.main" : "text.subtitle" }}
            />
            <Box component="span" sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {v.name}
            </Box>
            <Tooltip arrow title="Rename">
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  setNaming({ mode: "rename", id: v.id, value: v.name });
                  setAnchor(null);
                }}
              >
                <Iconify icon="solar:pen-linear" width={13} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </Tooltip>
            <Tooltip arrow title="Delete view">
              <IconButton size="small" onClick={(e) => { e.stopPropagation(); onDelete(v.id); }}>
                <Iconify icon="solar:trash-bin-trash-linear" width={13} sx={{ color: "text.subtitle" }} />
              </IconButton>
            </Tooltip>
          </MenuItem>
        ))}

        <Divider sx={{ my: 0.5 }} />

        {/* Updating only makes sense on a saved view you have since changed. */}
        {activeId && dirty && (
          <MenuItem onClick={() => { onUpdate(activeId); setAnchor(null); }} sx={{ typography: "s2", py: 0.875 }}>
            <Iconify icon="solar:diskette-linear" width={16} sx={{ mr: 1.25, color: "text.subtitle" }} />
            Update “{active?.name}”
          </MenuItem>
        )}
        <MenuItem
          onClick={() => { setNaming({ mode: "save", value: "" }); setAnchor(null); }}
          sx={{ typography: "s2", py: 0.875 }}
        >
          <Iconify icon="solar:add-circle-linear" width={16} sx={{ mr: 1.25, color: "text.subtitle" }} />
          Save current as new view
        </MenuItem>
      </Menu>

      {/*
        Naming is a small card hung off the same button, not a modal: it is one
        field, and a dialog that dims the screen to ask for a name makes the
        thing you are naming disappear while you name it.
      */}
      <Popover
        open={!!naming}
        anchorEl={buttonRef.current}
        onClose={() => setNaming(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 340, borderRadius: 1.5 } } }}
      >
        <Box sx={{ p: 2.5 }}>
          <Stack direction="row" alignItems="flex-start" spacing={2}>
            <Box flex={1} minWidth={0}>
              <Typography sx={{ typography: "s1", fontWeight: 700 }}>
                {naming?.mode === "save" ? "Save view" : "Rename view"}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>
                {naming?.mode === "save"
                  ? "Save the current filters and layout for quick access later."
                  : "Give this view a name that says what it shows."}
              </Typography>
            </Box>
            <IconButton size="small" onClick={() => setNaming(null)} sx={{ mt: -0.5, mr: -0.5 }}>
              <Iconify icon="mingcute:close-line" width={16} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>

          <TextField
            autoFocus fullWidth size="small"
            label={<>View name <Box component="span" sx={{ color: "error.main" }}>*</Box></>}
            placeholder="Enter your view name"
            value={naming?.value || ""}
            onChange={(e) => setNaming({ ...naming, value: e.target.value })}
            onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
            sx={{ mt: 2.5, "& .MuiInputBase-input": { typography: "s2" } }}
          />

          <Stack direction="row" spacing={1} justifyContent="flex-end" sx={{ mt: 2 }}>
            <Button
              size="small" variant="outlined" color="inherit"
              onClick={() => setNaming(null)}
              sx={{ typography: "s2", fontWeight: 700, borderColor: "divider", px: 2 }}
            >
              Cancel
            </Button>
            <Button
              size="small" variant="contained"
              disabled={!(naming?.value || "").trim()}
              onClick={commit}
              sx={{ typography: "s2", fontWeight: 700, px: 2 }}
            >
              {naming?.mode === "save" ? "Save view" : "Rename"}
            </Button>
          </Stack>
        </Box>
      </Popover>
    </>
  );
}

SavedViews.propTypes = {
  views: PropTypes.array, activeId: PropTypes.string, dirty: PropTypes.bool,
  onApply: PropTypes.func, onSave: PropTypes.func, onUpdate: PropTypes.func,
  onRename: PropTypes.func, onDelete: PropTypes.func,
};

/**
 * Filter, Display and Actions — the controls that belong to the whole screen.
 *
 * They sit in the page header rather than above the table because that is
 * their scope: Filter decides which scenarios exist, Display decides how the
 * whole comparison is drawn (including which of the three views you are in),
 * and Actions works on the run set. Search and Show diff live above the table
 * instead, because those two only concern the rows underneath them.
 */
export default function CompareActions({
  filters, onFilters, evals,
  view, onView, onSaveDefault, onResetView,
  selectedCount,
  onExport, onCopyLink, onRerun, onRegrade, onOptimize,
}) {
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [displayAnchor, setDisplayAnchor] = useState(null);
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const filterCount = activeFilterCount(filters);

  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
      <Button
        size="small"
        onClick={(e) => setFilterAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:filter-linear" width={15} />}
        sx={{
          typography: "s2", fontWeight: 600,
          color: filterCount ? "primary.main" : "text.secondary",
          border: "1px solid", borderColor: filterCount ? "primary.main" : "divider",
        }}
      >
        Filter{filterCount ? ` · ${filterCount}` : ""}
      </Button>

      <Button
        size="small"
        onClick={(e) => setDisplayAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:settings-minimalistic-linear" width={15} />}
        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", border: "1px solid", borderColor: "divider" }}
      >
        Display
      </Button>

      <Button
        size="small"
        onClick={(e) => setActionsAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:bolt-linear" width={15} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={13} />}
        sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", border: "1px solid", borderColor: "divider" }}
      >
        Actions
      </Button>

      {/* The platform's own filter panel — same property / operator / value
          rows, Basic and Query tabs, Add filter, Clear all, Apply — rather than
          a bespoke one that behaves almost but not quite like it. */}
      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields(evals)}
        currentFilters={filters}
        onApply={onFilters}
        aiPlaceholder="e.g. 'blockers that regressed'"
      />
      <DisplayPanel
        anchor={displayAnchor}
        onClose={() => setDisplayAnchor(null)}
        view={view}
        onView={onView}
        onSaveDefault={onSaveDefault}
        onResetView={onResetView}
      />
      <Menu
        open={!!actionsAnchor}
        anchorEl={actionsAnchor}
        onClose={() => setActionsAnchor(null)}
        slotProps={{ paper: { sx: { width: 260 } } }}
      >
        <ActionItem
          icon="solar:refresh-circle-linear"
          label={selectedCount ? `Re-run ${selectedCount} scenario${selectedCount === 1 ? "" : "s"}` : "Re-run selected scenarios"}
          disabled={!selectedCount}
          onClick={() => { setActionsAnchor(null); onRerun(); }}
        />
        {/* The third replay mode. A grader change does not need the calls made
            again — the evidence is already recorded, and re-running would cost
            money and change the sample. */}
        <ActionItem
          icon="solar:checklist-minimalistic-linear"
          label={selectedCount ? `Re-grade ${selectedCount} scenario${selectedCount === 1 ? "" : "s"}` : "Re-grade from recorded evidence"}
          disabled={!selectedCount}
          onClick={() => { setActionsAnchor(null); onRegrade(); }}
        />
        <ActionItem
          icon="solar:magic-stick-3-linear"
          label="Send selected to Optimize"
          disabled={!selectedCount}
          onClick={() => { setActionsAnchor(null); onOptimize(); }}
        />
        <Divider sx={{ my: 0.5 }} />
        <ActionItem
          icon="solar:download-minimalistic-linear"
          label="Export comparison"
          onClick={() => { setActionsAnchor(null); onExport(); }}
        />
        <ActionItem
          icon="solar:link-linear"
          label="Copy link to this view"
          onClick={() => { setActionsAnchor(null); onCopyLink(); }}
        />
      </Menu>
    </Stack>
  );
}

CompareActions.propTypes = {
  filters: PropTypes.array, onFilters: PropTypes.func, evals: PropTypes.array,
  view: PropTypes.object, onView: PropTypes.func,
  onSaveDefault: PropTypes.func, onResetView: PropTypes.func,
  selectedCount: PropTypes.number,
  onExport: PropTypes.func, onCopyLink: PropTypes.func,
  onRerun: PropTypes.func, onRegrade: PropTypes.func, onOptimize: PropTypes.func,
};

/** Search and Show diff: the two controls that only concern the rows below. */
export function CompareSearchBar({ query, onQuery, view, onView }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.5} sx={{ flexShrink: 0 }}>
      <TextField
        size="small"
        placeholder="Search scenarios, personas, findings"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        InputProps={{
          sx: { typography: "s2" },
          startAdornment: (
            <InputAdornment position="start">
              <Iconify icon="solar:magnifer-linear" width={15} sx={{ color: "text.subtitle" }} />
            </InputAdornment>
          ),
        }}
        sx={{ width: 280, "& .MuiInputBase-input": { py: 0.75 } }}
      />

      {/* A switch rather than a menu item: it changes what every row says, and
          that should be reversible without opening anything. */}
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Switch
          size="small"
          checked={view.diff}
          onChange={() => onView({ ...view, diff: !view.diff })}
        />
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>Show diff</Typography>
        <Tooltip
          arrow
          title="Replaces each run's outcome with what it did differently from the baseline — tools it stopped calling, turns it added, where the conversation first diverged."
        >
          <Box sx={{ display: "flex" }}>
            <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
          </Box>
        </Tooltip>
      </Stack>
    </Stack>
  );
}

CompareSearchBar.propTypes = {
  query: PropTypes.string, onQuery: PropTypes.func,
  view: PropTypes.object, onView: PropTypes.func,
};


function ActionItem({ icon, label, onClick, disabled }) {
  return (
    <MenuItem onClick={onClick} disabled={disabled} sx={{ typography: "s2", py: 1 }}>
      <Iconify icon={icon} width={16} sx={{ mr: 1.25, color: "text.subtitle" }} />
      {label}
    </MenuItem>
  );
}
ActionItem.propTypes = {
  icon: PropTypes.string, label: PropTypes.string,
  onClick: PropTypes.func, disabled: PropTypes.bool,
};

/**
 * What a row looks like.
 *
 * Ends with "Set as default view", which is the honest version of saving a
 * view: this is a comparison of runs that will not exist next week, so what is
 * worth keeping is the *shape* you like reading, not this particular set.
 */
function DisplayPanel({ anchor, onClose, view, onView, onSaveDefault, onResetView }) {
  const set = (patch) => onView({ ...view, ...patch });
  const toggleColumn = (id) => set({ columns: { ...view.columns, [id]: !view.columns[id] } });
  const toggleQuick = (id) => set({
    quick: view.quick.includes(id) ? view.quick.filter((q) => q !== id) : [...view.quick, id],
  });

  return (
    <Popover
      open={!!anchor}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 320, borderRadius: 1.5, maxHeight: 560 } } }}
    >
      <Stack direction="row" spacing={0.75} sx={{ p: 1.25 }}>
        {VIEWS.map((v) => {
          const on = view.view === v.id;
          return (
            <Tooltip key={v.id} arrow title={v.blurb}>
              <Box
                onClick={() => set({ view: v.id })}
                sx={{
                  flex: 1, py: 1, borderRadius: 1, cursor: "pointer", textAlign: "center",
                  bgcolor: (t) => (on ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.18 : 0.1) : "transparent"),
                  color: on ? "primary.main" : "text.secondary",
                  "&:hover": { bgcolor: on ? undefined : "action.hover" },
                }}
              >
                <Iconify icon={v.icon} width={16} />
                <Typography sx={{ typography: "s3", fontWeight: 600, mt: 0.25 }}>{v.label}</Typography>
              </Box>
            </Tooltip>
          );
        })}
      </Stack>

      <Divider />

      <Section title="Rows">
        <Row label="Row height">
          <SegmentedTabs
            value={view.rowHeight}
            onChange={(_, v) => set({ rowHeight: v })}
            sx={{ "& .MuiTab-root": { minHeight: 26, px: 1 } }}
          >
            {ROW_HEIGHTS.map((h) => <Tab key={h.id} value={h.id} label={h.label} />)}
          </SegmentedTabs>
        </Row>
        <Row label="Group by">
          <Select
            value={view.group === "none" ? "" : view.group}
            onChange={(v) => set({ group: v || "none" })}
            options={GROUPINGS.filter((g) => g.id !== "none")}
            placeholder="Nothing"
          />
        </Row>
        <Row label="Sort">
          <Select
            value={view.sort}
            onChange={(v) => set({ sort: v || "movement" })}
            options={SORTS}
            placeholder="Regressions first"
          />
        </Row>
      </Section>

      <Divider />

      <Section title="Columns">
        {[
          { id: "duration", label: "Duration" },
          { id: "tokens", label: "Tokens" },
          { id: "cost", label: "Cost" },
          { id: "scorers", label: "Grader scores" },
        ].map((c) => (
          <Check key={c.id} label={c.label} checked={view.columns[c.id]} onChange={() => toggleColumn(c.id)} />
        ))}
      </Section>

      <Divider />

      <Section title="Charts">
        <Check label="Score distribution" checked={view.showChart} onChange={() => set({ showChart: !view.showChart })} />
      </Section>

      <Divider />

      <Section title="Filter rows">
        {QUICK_FILTERS.map((q) => (
          <Check
            key={q.id}
            icon={q.icon}
            label={q.label}
            checked={view.quick.includes(q.id)}
            onChange={() => toggleQuick(q.id)}
          />
        ))}
      </Section>

      <Divider />

      {/*
        Saving the *shape*, not the comparison. These runs will not exist next
        quarter, but "grouped by failure mode, regressions first, graders on"
        is how this team reads a comparison — so that is what is worth keeping.
      */}
      <Stack sx={{ px: 1, py: 0.75 }}>
        <MenuItem onClick={() => { onSaveDefault(); onClose(); }} sx={{ typography: "s2", borderRadius: 0.75 }}>
          <Iconify icon="solar:eye-linear" width={16} sx={{ mr: 1.25, color: "text.subtitle" }} />
          Set as default view
        </MenuItem>
        <MenuItem onClick={() => { onResetView(); onClose(); }} sx={{ typography: "s2", borderRadius: 0.75 }}>
          <Iconify icon="solar:refresh-linear" width={16} sx={{ mr: 1.25, color: "text.subtitle" }} />
          Reset view
        </MenuItem>
      </Stack>
    </Popover>
  );
}
DisplayPanel.propTypes = {
  anchor: PropTypes.any, onClose: PropTypes.func,
  view: PropTypes.object, onView: PropTypes.func,
  onSaveDefault: PropTypes.func, onResetView: PropTypes.func,
};

function Section({ title, children }) {
  return (
    <Box sx={{ px: 2, py: 1.25 }}>
      <Typography
        sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}
      >
        {title}
      </Typography>
      <Stack spacing={0.75}>{children}</Stack>
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

function Row({ label, children }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>{label}</Typography>
      <Box sx={{ flexShrink: 0 }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

/** The small dropdown the Display panel uses for grouping and sorting. */
function Select({ value, onChange, options, placeholder = "Any" }) {
  return (
    <TextField
      select size="small"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      SelectProps={{ displayEmpty: true }}
      sx={{ minWidth: 150, "& .MuiInputBase-input": { typography: "s2", py: 0.5 } }}
    >
      <MenuItem value="" sx={{ typography: "s2", color: "text.subtitle" }}>{placeholder}</MenuItem>
      {options.map((o) => (
        <MenuItem key={o.id} value={o.id} sx={{ typography: "s2" }}>{o.label}</MenuItem>
      ))}
    </TextField>
  );
}
Select.propTypes = {
  value: PropTypes.string, onChange: PropTypes.func,
  options: PropTypes.array, placeholder: PropTypes.string,
};

function Check({ label, checked, onChange, icon }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      onClick={onChange}
      sx={{ cursor: "pointer", borderRadius: 0.75, "&:hover": { bgcolor: "action.hover" } }}
    >
      <Checkbox size="small" checked={checked} sx={{ p: 0.5, pointerEvents: "none" }} />
      {icon && <Iconify icon={icon} width={14} sx={{ color: "text.subtitle" }} />}
      <Typography sx={{ typography: "s2", flex: 1 }}>{label}</Typography>
    </Stack>
  );
}
Check.propTypes = {
  label: PropTypes.string, checked: PropTypes.bool,
  onChange: PropTypes.func, icon: PropTypes.string,
};



