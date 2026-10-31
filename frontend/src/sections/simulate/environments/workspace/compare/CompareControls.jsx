import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Divider,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Tab,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { SegmentedTabs } from "src/components/tabs/tabs";
import SoonChip from "../../components/SoonChip";
import {
  COLUMNS,
  GROUPINGS,
  QUICK_FILTERS,
  ROW_HEIGHTS,
  SOON_GROUPINGS,
  SOON_QUICK_FILTERS,
  SORTS,
  VIEWS,
} from "./compareView";

const OUTLINE_SX = {
  color: "text.secondary",
  border: "1px solid",
  borderColor: "divider",
};
const SECTION_TITLE_SX = {
  typography: "s3",
  fontWeight: "fontWeightSemiBold",
  color: "text.subtitle",
};

const SAVE_UNAVAILABLE = "Saving views isn't available yet.";

/**
 * Saved views: a switcher that names the view you're in, lists the saved
 * ones and offers to save the current reading. The dot says the reading has
 * changed since the view was saved. Without `onSave`, saving shows disabled.
 */
export function SavedViews({
  views,
  activeId,
  dirty,
  onApply,
  onSave,
  onUpdate,
  onRename,
  onDelete,
}) {
  const buttonRef = useRef(null);
  const [anchor, setAnchor] = useState(null);
  const [naming, setNaming] = useState(null);
  const active = views.find((v) => v.id === activeId);

  const commit = () => {
    const name = (naming?.value || "").trim();
    if (!name) return;
    if (naming.mode === "save") onSave(name);
    else onRename(naming.id, name);
    setNaming(null);
  };

  const itemIcon = (on, idle) => (
    <Iconify
      icon={on ? "solar:check-circle-bold" : idle}
      width={16}
      sx={{ mr: 1.25, color: on ? "primary.main" : "text.subtitle" }}
    />
  );

  return (
    <>
      <Button
        size="small"
        ref={buttonRef}
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:bookmark-linear" width={16} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        sx={{ ...OUTLINE_SX, maxWidth: 240 }}
      >
        <Box
          component="span"
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {active ? active.name : "All scenarios"}
        </Box>
        {dirty && (
          <Box
            component="span"
            aria-label="Changed since saved"
            sx={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              bgcolor: "primary.main",
              ml: 0.75,
              flexShrink: 0,
            }}
          />
        )}
      </Button>

      <Menu
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        <Typography sx={{ ...SECTION_TITLE_SX, px: 2, pt: 1, pb: 0.5 }}>
          Views
        </Typography>
        <MenuItem
          onClick={() => {
            onApply(null);
            setAnchor(null);
          }}
          sx={{ typography: "s2", py: 0.875 }}
        >
          {itemIcon(!activeId, "solar:list-linear")}
          All scenarios
        </MenuItem>
        {views.map((v) => (
          <MenuItem
            key={v.id}
            onClick={() => {
              onApply(v.id);
              setAnchor(null);
            }}
            sx={{ typography: "s2", py: 0.875 }}
          >
            {itemIcon(v.id === activeId, "solar:bookmark-linear")}
            <Box
              component="span"
              sx={{
                flex: 1,
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {v.name}
            </Box>
            <CustomTooltip show arrow size="small" title="Rename">
              <IconButton
                size="small"
                aria-label={`Rename ${v.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setNaming({ mode: "rename", id: v.id, value: v.name });
                  setAnchor(null);
                }}
              >
                <Iconify
                  icon="solar:pen-linear"
                  width={14}
                  sx={{ color: "text.subtitle" }}
                />
              </IconButton>
            </CustomTooltip>
            <CustomTooltip show arrow size="small" title="Delete view">
              <IconButton
                size="small"
                aria-label={`Delete ${v.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(v.id);
                }}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={14}
                  sx={{ color: "text.subtitle" }}
                />
              </IconButton>
            </CustomTooltip>
          </MenuItem>
        ))}
        <Divider sx={{ my: 0.5 }} />
        {activeId && dirty && (
          <MenuItem
            onClick={() => {
              onUpdate(activeId);
              setAnchor(null);
            }}
            sx={{ typography: "s2", py: 0.875 }}
          >
            {itemIcon(false, "solar:diskette-linear")}
            {`Update "${active?.name}"`}
          </MenuItem>
        )}
        <CustomTooltip
          show={!onSave}
          arrow
          size="small"
          title={SAVE_UNAVAILABLE}
        >
          <span>
            <MenuItem
              disabled={!onSave}
              onClick={() => {
                setNaming({ mode: "save", value: "" });
                setAnchor(null);
              }}
              sx={{ typography: "s2", py: 0.875 }}
            >
              {itemIcon(false, "solar:add-circle-linear")}
              Save current as new view
            </MenuItem>
          </span>
        </CustomTooltip>
      </Menu>

      <Popover
        open={!!naming}
        anchorEl={buttonRef.current}
        onClose={() => setNaming(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 340, borderRadius: "8px" } } }}
      >
        <Box sx={{ p: 2.5 }}>
          <Typography
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            {naming?.mode === "save" ? "Save view" : "Rename view"}
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
          >
            {naming?.mode === "save"
              ? "Save the current layout for quick access later."
              : "Give this view a name that says what it shows."}
          </Typography>
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="View name"
            required
            placeholder="Enter your view name"
            value={naming?.value || ""}
            onChange={(e) => setNaming({ ...naming, value: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
            }}
            sx={{ mt: 2.5, "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack
            direction="row"
            spacing={1}
            justifyContent="flex-end"
            sx={{ mt: 2 }}
          >
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() => setNaming(null)}
              sx={{ borderColor: "divider" }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              disabled={!(naming?.value || "").trim()}
              onClick={commit}
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
  views: PropTypes.array.isRequired,
  activeId: PropTypes.string,
  dirty: PropTypes.bool,
  onApply: PropTypes.func.isRequired,
  // Leave out while views can't be saved.
  onSave: PropTypes.func,
  onUpdate: PropTypes.func.isRequired,
  onRename: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
};

function ActionItem({ icon, label, onClick }) {
  return (
    <MenuItem onClick={onClick} sx={{ typography: "s2", py: 1 }}>
      <Iconify
        icon={icon}
        width={16}
        sx={{ mr: 1.25, color: "text.subtitle" }}
      />
      {label}
    </MenuItem>
  );
}
ActionItem.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  onClick: PropTypes.func,
};

/**
 * Group by, Display and Actions: the controls for the whole comparison.
 * Search and Show diff sit above the table, since they concern only its rows.
 */
export default function CompareActions({
  view,
  onView,
  onSaveDefault,
  onResetView,
  onExport,
  onCopyLink,
}) {
  const [groupAnchor, setGroupAnchor] = useState(null);
  const [displayAnchor, setDisplayAnchor] = useState(null);
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const group = GROUPINGS.find((g) => g.id === view.group) || GROUPINGS[0];

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{ flexShrink: 0 }}
    >
      <Button
        size="small"
        onClick={(e) => setGroupAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:widget-linear" width={16} />}
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.subtitle" }}
          />
        }
        sx={OUTLINE_SX}
      >
        Group by
        <Box component="span" sx={{ mx: 0.5, color: "text.subtitle" }}>
          ·
        </Box>
        <Box
          component="span"
          sx={{ color: "primary.main", fontWeight: "fontWeightSemiBold" }}
        >
          {group.label}
        </Box>
      </Button>
      <Menu
        anchorEl={groupAnchor}
        open={!!groupAnchor}
        onClose={() => setGroupAnchor(null)}
      >
        {GROUPINGS.map((g) => (
          <MenuItem
            key={g.id}
            selected={g.id === view.group}
            onClick={() => {
              onView({ ...view, group: g.id });
              setGroupAnchor(null);
            }}
            sx={{ typography: "s2", py: 0.75 }}
          >
            {g.label}
          </MenuItem>
        ))}
        {SOON_GROUPINGS.map((g) => (
          <Stack
            key={g.id}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 2, py: 0.75 }}
          >
            <Typography
              sx={{ typography: "s2", color: "text.disabled", flex: 1 }}
            >
              {g.label}
            </Typography>
            <SoonChip label={g.label} what={g.what} size="tiny" />
          </Stack>
        ))}
      </Menu>

      <Button
        size="small"
        onClick={(e) => setDisplayAnchor(e.currentTarget)}
        startIcon={
          <Iconify icon="solar:settings-minimalistic-linear" width={16} />
        }
        sx={OUTLINE_SX}
      >
        Display
      </Button>
      <Button
        size="small"
        onClick={(e) => setActionsAnchor(e.currentTarget)}
        startIcon={<Iconify icon="solar:bolt-linear" width={16} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={14} />}
        sx={OUTLINE_SX}
      >
        Actions
      </Button>

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
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        <ActionItem
          icon="solar:download-minimalistic-linear"
          label="Export comparison"
          onClick={() => {
            setActionsAnchor(null);
            onExport();
          }}
        />
        <ActionItem
          icon="solar:link-linear"
          label="Copy link to this view"
          onClick={() => {
            setActionsAnchor(null);
            onCopyLink();
          }}
        />
      </Menu>
    </Stack>
  );
}
CompareActions.propTypes = {
  view: PropTypes.object.isRequired,
  onView: PropTypes.func.isRequired,
  // Leave out while views can't be saved.
  onSaveDefault: PropTypes.func,
  onResetView: PropTypes.func.isRequired,
  onExport: PropTypes.func.isRequired,
  onCopyLink: PropTypes.func.isRequired,
};

/** Search, and Show diff (not in the API yet): they concern only the rows. */
export function CompareSearchBar({ query, onQuery }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ flexShrink: 0 }}
    >
      <TextField
        size="small"
        placeholder="Search scenarios, goals, personas"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        InputProps={{
          sx: { typography: "s2" },
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="solar:magnifer-linear"
                width={16}
                sx={{ color: "text.subtitle" }}
              />
            </InputAdornment>
          ),
        }}
        sx={{ width: 280, "& .MuiInputBase-input": { py: 0.75 } }}
      />
      <SoonChip
        label="Show diff"
        what="A diff of what each run did differently"
      />
    </Stack>
  );
}
CompareSearchBar.propTypes = {
  query: PropTypes.string.isRequired,
  onQuery: PropTypes.func.isRequired,
};

function Section({ title, children }) {
  return (
    <Box sx={{ px: 2, py: 1.25 }}>
      <Typography sx={{ ...SECTION_TITLE_SX, mb: 0.75 }}>{title}</Typography>
      <Stack spacing={0.75}>{children}</Stack>
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

function Check({ label, checked, onChange, icon, soon }) {
  if (soon) {
    return (
      <Stack direction="row" alignItems="center" spacing={1} sx={{ pl: 0.5 }}>
        <Typography sx={{ typography: "s2", flex: 1, color: "text.disabled" }}>
          {label}
        </Typography>
        <SoonChip
          label={label}
          what={soon.what}
          plural={soon.plural}
          size="tiny"
        />
      </Stack>
    );
  }
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      onClick={(e) => {
        if (!(e.target instanceof HTMLInputElement)) onChange();
      }}
      sx={{
        cursor: "pointer",
        borderRadius: "4px",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Checkbox
        size="small"
        checked={checked}
        onChange={onChange}
        inputProps={{ "aria-label": label }}
        sx={{ p: 0.5 }}
      />
      {icon && (
        <Iconify icon={icon} width={14} sx={{ color: "text.subtitle" }} />
      )}
      <Typography sx={{ typography: "s2", flex: 1 }}>{label}</Typography>
    </Stack>
  );
}
Check.propTypes = {
  label: PropTypes.string,
  checked: PropTypes.bool,
  onChange: PropTypes.func,
  icon: PropTypes.string,
  soon: PropTypes.object,
};

// What a row looks like. Ends with "Set as default view": the shape this
// team likes reading, kept for every comparison in this environment. Without
// `onSaveDefault` it shows disabled, since views can't be saved yet.
function DisplayPanel({
  anchor,
  onClose,
  view,
  onView,
  onSaveDefault,
  onResetView,
}) {
  const set = (patch) => onView({ ...view, ...patch });
  const toggleQuick = (id) =>
    set({
      quick: view.quick.includes(id)
        ? view.quick.filter((q) => q !== id)
        : [...view.quick, id],
    });

  return (
    <Popover
      open={!!anchor}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: { sx: { width: 320, borderRadius: "8px", maxHeight: 560 } },
      }}
    >
      {VIEWS.length > 1 && (
        <>
          <Stack direction="row" spacing={0.75} sx={{ p: 1.25 }}>
            {VIEWS.map((v) => {
              const on = view.view === v.id;
              return (
                <CustomTooltip
                  key={v.id}
                  show
                  arrow
                  size="small"
                  title={v.blurb}
                >
                  <ButtonBase
                    aria-label={v.label}
                    aria-pressed={on}
                    onClick={() => set({ view: v.id })}
                    sx={{
                      flex: 1,
                      py: 1,
                      borderRadius: 1,
                      flexDirection: "column",
                      textAlign: "center",
                      bgcolor: (t) =>
                        on
                          ? alpha(t.palette.primary.main, 0.08)
                          : "transparent",
                      color: on ? "primary.main" : "text.secondary",
                      "&:hover": { bgcolor: on ? undefined : "action.hover" },
                    }}
                  >
                    <Iconify icon={v.icon} width={16} />
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: "fontWeightSemiBold",
                        mt: 0.25,
                      }}
                    >
                      {v.label}
                    </Typography>
                  </ButtonBase>
                </CustomTooltip>
              );
            })}
          </Stack>
          <Divider />
        </>
      )}

      <Section title="Rows">
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
          >
            Row height
          </Typography>
          <Box sx={{ "& .MuiTab-root": { minHeight: 26, px: 1 } }}>
            <SegmentedTabs
              value={view.rowHeight}
              onChange={(_, v) => set({ rowHeight: v })}
            >
              {ROW_HEIGHTS.map((h) => (
                <Tab key={h.id} value={h.id} label={h.label} />
              ))}
            </SegmentedTabs>
          </Box>
        </Stack>
        <Stack direction="row" alignItems="center" spacing={1.5}>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", flex: 1 }}
          >
            Sort
          </Typography>
          <TextField
            select
            size="small"
            value={view.sort}
            onChange={(e) => set({ sort: e.target.value })}
            inputProps={{ "aria-label": "Sort" }}
            sx={{
              minWidth: 150,
              "& .MuiInputBase-input": { typography: "s2", py: 0.5 },
            }}
          >
            {SORTS.map((o) => (
              <MenuItem key={o.id} value={o.id} sx={{ typography: "s2" }}>
                {o.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </Section>
      <Divider />

      <Section title="Columns">
        {COLUMNS.map((c) => (
          <Check
            key={c.id}
            label={c.label}
            checked={!!view.columns[c.id]}
            onChange={() =>
              set({ columns: { ...view.columns, [c.id]: !view.columns[c.id] } })
            }
          />
        ))}
      </Section>
      <Divider />

      <Section title="Charts">
        <Check
          label="Score distribution"
          checked={!!view.showChart}
          onChange={() => set({ showChart: !view.showChart })}
        />
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
        {SOON_QUICK_FILTERS.map((q) => (
          <Check key={q.id} label={q.label} soon={q} />
        ))}
      </Section>
      <Divider />

      <Stack sx={{ px: 1, py: 0.75 }}>
        <CustomTooltip
          show={!onSaveDefault}
          arrow
          size="small"
          title={SAVE_UNAVAILABLE}
        >
          <span>
            <MenuItem
              disabled={!onSaveDefault}
              onClick={() => {
                onSaveDefault();
                onClose();
              }}
              sx={{ typography: "s2", borderRadius: "4px" }}
            >
              <Iconify
                icon="solar:eye-linear"
                width={16}
                sx={{ mr: 1.25, color: "text.subtitle" }}
              />
              Set as default view
            </MenuItem>
          </span>
        </CustomTooltip>
        <MenuItem
          onClick={() => {
            onResetView();
            onClose();
          }}
          sx={{ typography: "s2", borderRadius: "4px" }}
        >
          <Iconify
            icon="solar:refresh-linear"
            width={16}
            sx={{ mr: 1.25, color: "text.subtitle" }}
          />
          Reset view
        </MenuItem>
      </Stack>
    </Popover>
  );
}
DisplayPanel.propTypes = {
  anchor: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  view: PropTypes.object.isRequired,
  onView: PropTypes.func.isRequired,
  // Leave out while views can't be saved.
  onSaveDefault: PropTypes.func,
  onResetView: PropTypes.func.isRequired,
};
