// Row layouts (W9-4): the Customize panel (right, 420 px). Fields: drag to
// reorder (pointer or keyboard), show / hide, View as, Side when Two columns
// is on. Then the Buttons and Custom renderer sections the host passes, and
// Save layout: a name, For everyone on this dataset / Just for me.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  Drawer,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import { CUSTOM_VIEW, moveField, patchField, viewOptions } from "./layoutModel";

export const PANEL_WIDTH = 420;
export const VIEWER_SHARE_HINT =
  "Viewers can save layouts for themselves only.";

const small = { typography: "s2", "& .MuiSelect-select": { py: 0.25, pl: 1 } };

const FieldRow = ({ field, two, views, onPatch }) => {
  const sortable = useSortable({ id: field.column_id });
  const style = {
    transform: CSS.Transform.toString(sortable.transform),
    transition: sortable.transition,
    opacity: sortable.isDragging ? 0.6 : 1,
  };
  const name = field.column_name;
  return (
    <Stack
      ref={sortable.setNodeRef}
      style={style}
      direction="row"
      alignItems="center"
      gap={0.5}
      data-testid={`panel-field-${name}`}
      sx={{
        py: 0.5,
        pr: 0.5,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      <IconButton
        size="small"
        aria-label={`Move ${name}`}
        {...sortable.attributes}
        {...sortable.listeners}
        sx={{ cursor: "grab", p: 0.5, color: "text.disabled" }}
      >
        <Iconify icon="mdi:drag-vertical" width={18} />
      </IconButton>
      <IconButton
        size="small"
        aria-label={field.hidden ? `Show ${name}` : `Hide ${name}`}
        aria-pressed={!field.hidden}
        onClick={() => onPatch({ hidden: !field.hidden })}
        sx={{ p: 0.5 }}
      >
        <Iconify
          icon={field.hidden ? "mdi:eye-off-outline" : "mdi:eye-outline"}
          width={16}
        />
      </IconButton>
      <Typography
        typography="s1"
        noWrap
        sx={{ flex: 1, minWidth: 0 }}
        color={field.hidden ? "text.disabled" : "text.primary"}
      >
        {name}
      </Typography>
      <Select
        size="small"
        value={views.some((v) => v.value === field.view) ? field.view : "auto"}
        onChange={(e) => onPatch({ view: e.target.value })}
        inputProps={{ "aria-label": `View ${name} as` }}
        sx={{ ...small, minWidth: 96 }}
      >
        {views.map((v) => (
          <MenuItem key={v.value} value={v.value} sx={{ typography: "s2" }}>
            {v.label}
          </MenuItem>
        ))}
      </Select>
      {two && (
        <Select
          size="small"
          value={field.side}
          onChange={(e) => onPatch({ side: e.target.value })}
          inputProps={{ "aria-label": `Side of ${name}` }}
          sx={{ ...small, minWidth: 72 }}
        >
          <MenuItem value="left" sx={{ typography: "s2" }}>
            Left
          </MenuItem>
          <MenuItem value="right" sx={{ typography: "s2" }}>
            Right
          </MenuItem>
        </Select>
      )}
    </Stack>
  );
};
FieldRow.propTypes = {
  field: PropTypes.object.isRequired,
  two: PropTypes.bool,
  views: PropTypes.array.isRequired,
  onPatch: PropTypes.func.isRequired,
};

const Section = ({ title, hint, children }) => (
  <Box sx={{ px: 2, py: 1.5 }}>
    <Typography typography="s1" fontWeight="fontWeightMedium">
      {title}
    </Typography>
    {hint && (
      <Typography typography="s3" color="text.secondary" component="p">
        {hint}
      </Typography>
    )}
    <Box sx={{ mt: 1 }}>{children}</Box>
  </Box>
);
Section.propTypes = {
  title: PropTypes.string.isRequired,
  hint: PropTypes.string,
  children: PropTypes.node,
};

/**
 * @param {{open: boolean, draft: object, onDraft: Function, problems: string[],
 *   viewer: boolean, editing: object|null, saving: boolean,
 *   onSave: (name: string, shared: boolean) => void, onClose: Function,
 *   buttons?: React.ReactNode, renderer?: React.ReactNode}} props
 *   `draft` = the layout being edited (fields cover every column);
 *   `editing` = the saved view it started from, when the user may change it.
 */
const LayoutPanel = ({
  open,
  draft,
  onDraft,
  problems,
  viewer,
  editing,
  saving,
  onSave,
  onClose,
  buttons,
  renderer,
}) => {
  const [name, setName] = useState(editing?.name ?? "");
  const [shared, setShared] = useState(
    viewer ? false : editing ? editing.shared === true : true,
  );
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const views = useMemo(
    () =>
      viewOptions().filter((v) => v.value !== CUSTOM_VIEW || draft.renderer),
    [draft.renderer],
  );
  const ids = draft.fields.map((f) => f.column_id);
  const onDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return;
    onDraft(moveField(draft, ids.indexOf(active.id), ids.indexOf(over.id)));
  };
  const trimmed = name.trim();
  const sameName =
    editing && trimmed.toLowerCase() === editing.name.toLowerCase();
  const nameProblem = !trimmed ? "Give the layout a name." : null;
  const blocked = Boolean(nameProblem) || problems.length > 0;
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: PANEL_WIDTH, maxWidth: "100vw" } }}
      data-testid="layout-panel"
    >
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          typography="m3"
          fontWeight="fontWeightMedium"
          sx={{ flex: 1 }}
        >
          Customize layout
        </Typography>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <Section
          title="Fields"
          hint="Drag to reorder. Hidden fields stay one click away under the row."
        >
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={draft.columns === 2}
                onChange={(e) =>
                  onDraft({ ...draft, columns: e.target.checked ? 2 : 1 })
                }
              />
            }
            label={<Typography typography="s2">Two columns</Typography>}
            sx={{ ml: 0, mb: 0.5 }}
          />
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={onDragEnd}
          >
            <SortableContext items={ids} strategy={verticalListSortingStrategy}>
              <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
                {draft.fields.map((f) => (
                  <FieldRow
                    key={f.column_id}
                    field={f}
                    two={draft.columns === 2}
                    views={views}
                    onPatch={(patch) =>
                      onDraft(patchField(draft, f.column_id, patch))
                    }
                  />
                ))}
              </Box>
            </SortableContext>
          </DndContext>
        </Section>
        {buttons && (
          <>
            <Divider />
            {buttons}
          </>
        )}
        {renderer && (
          <>
            <Divider />
            {renderer}
          </>
        )}
      </Box>
      <Box
        sx={{ px: 2, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        {problems.length > 0 && (
          <Stack gap={0.25} sx={{ mb: 1 }} data-testid="layout-problems">
            {problems.map((p) => (
              <Typography key={p} typography="s3" color="error.main">
                {p}
              </Typography>
            ))}
          </Stack>
        )}
        <TextField
          size="small"
          fullWidth
          label="Layout name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          inputProps={{ "data-testid": "layout-name", maxLength: 80 }}
        />
        <RadioGroup
          value={shared ? "everyone" : "me"}
          onChange={(e) => setShared(e.target.value === "everyone")}
          sx={{ mt: 1 }}
        >
          <FormControlLabel
            value="everyone"
            disabled={viewer}
            control={<Radio size="small" />}
            label={
              <Typography typography="s2">
                For everyone on this dataset
              </Typography>
            }
          />
          {viewer && (
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ pl: 3.5, mt: -0.5 }}
            >
              {VIEWER_SHARE_HINT}
            </Typography>
          )}
          <FormControlLabel
            value="me"
            control={<Radio size="small" />}
            label={<Typography typography="s2">Just for me</Typography>}
          />
        </RadioGroup>
        <Stack direction="row" gap={1} justifyContent="flex-end" sx={{ mt: 1 }}>
          <Button size="small" color="inherit" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={blocked || saving}
            onClick={() =>
              onSave(trimmed, shared && !viewer, Boolean(sameName))
            }
            data-testid="layout-save"
          >
            {sameName ? "Save changes" : "Save layout"}
          </Button>
        </Stack>
      </Box>
    </Drawer>
  );
};

LayoutPanel.propTypes = {
  open: PropTypes.bool.isRequired,
  draft: PropTypes.object.isRequired,
  onDraft: PropTypes.func.isRequired,
  problems: PropTypes.arrayOf(PropTypes.string).isRequired,
  viewer: PropTypes.bool,
  editing: PropTypes.object,
  saving: PropTypes.bool,
  onSave: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
  buttons: PropTypes.node,
  renderer: PropTypes.node,
};

export default LayoutPanel;
