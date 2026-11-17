// Row layouts (W9-4, B3): the Buttons section of the Customize panel. Each
// kind the change-set can apply has its inputs here: Set a field (column +
// value), Set the row status (status + reason; archiving needs one), Add a
// tag. Kinds the branch can't apply are not offered (changeSetKinds.js).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  ARCHIVED_STATUS,
  CONTROL_KIND_LABEL,
  blankControl,
  cleanControl,
  controlSummary,
  validateControls,
} from "./controlOps";
import { availableControlKinds, rowStatusLabels } from "./changeSetKinds";
import { nextControlId, putControl } from "./layoutModel";

/**
 * @param {{draft: object, columns: object[], onDraft: Function,
 *   kinds?: string[], statuses?: object|null}} props
 *   `kinds` / `statuses` default to what this branch's change-set has.
 */
const ButtonsEditor = ({ draft, columns, onDraft, kinds, statuses }) => {
  const can = kinds ?? availableControlKinds();
  const labels = statuses === undefined ? rowStatusLabels() : statuses;
  const editable = columns.filter((c) => c.editable);
  // Set a field needs an editable column; the other kinds don't.
  const offered = can.filter((k) => k !== "set_field" || editable.length);
  const blank = () => ({ label: "", ...blankControl(offered[0], columns) });
  const [adding, setAdding] = useState(null);
  const problems = adding
    ? validateControls([adding], columns, offered, labels)
    : [];
  const add = () => {
    const id = nextControlId(draft.controls);
    onDraft(putControl(draft, id, cleanControl(id, adding)));
    setAdding(null);
  };
  const colName = (id) => columns.find((c) => c.id === id)?.name ?? "?";
  const set = (patch) => setAdding({ ...adding, ...patch });
  return (
    <Box sx={{ px: 2, py: 1.5 }} data-testid="panel-buttons">
      <Typography typography="s1" fontWeight="fontWeightMedium">
        Buttons
      </Typography>
      <Typography typography="s3" color="text.secondary" component="p">
        Each button adds a pending change to the open row; Save version keeps
        it.
      </Typography>
      <Stack gap={0.5} sx={{ mt: 1 }}>
        {(draft.controls ?? []).map((c) => (
          <Stack
            key={c.id}
            direction="row"
            alignItems="center"
            gap={1}
            data-testid={`panel-button-${c.label}`}
          >
            <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }} noWrap>
              {controlSummary(c, { colName, statuses: labels })}
            </Typography>
            <IconButton
              size="small"
              aria-label={`Remove the button ${c.label}`}
              onClick={() => onDraft(putControl(draft, c.id, null))}
            >
              <Iconify icon="mdi:close" width={16} />
            </IconButton>
          </Stack>
        ))}
      </Stack>
      {adding ? (
        <Stack gap={1} sx={{ mt: 1 }}>
          <TextField
            size="small"
            label="Button label"
            value={adding.label}
            onChange={(e) => set({ label: e.target.value })}
            inputProps={{ "data-testid": "button-label" }}
          />
          {offered.length > 1 && (
            <TextField
              select
              size="small"
              label="It does"
              value={adding.kind}
              onChange={(e) =>
                setAdding({
                  label: adding.label,
                  ...blankControl(e.target.value, columns),
                })
              }
              SelectProps={{ inputProps: { "data-testid": "button-kind" } }}
            >
              {offered.map((k) => (
                <MenuItem key={k} value={k}>
                  {CONTROL_KIND_LABEL[k]}
                </MenuItem>
              ))}
            </TextField>
          )}
          {adding.kind === "set_field" && (
            <Stack direction="row" gap={1}>
              <TextField
                select
                size="small"
                label="Sets"
                value={adding.column_id}
                onChange={(e) => set({ column_id: e.target.value })}
                sx={{ flex: 1 }}
                SelectProps={{ inputProps: { "data-testid": "button-column" } }}
              >
                {editable.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.name}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                size="small"
                label="To"
                value={adding.value}
                onChange={(e) => set({ value: e.target.value })}
                sx={{ flex: 1 }}
                inputProps={{ "data-testid": "button-value" }}
              />
            </Stack>
          )}
          {adding.kind === "set_status" && (
            <Stack direction="row" gap={1}>
              <TextField
                select
                size="small"
                label="Status"
                value={adding.status}
                onChange={(e) => set({ status: e.target.value })}
                sx={{ flex: 1 }}
                SelectProps={{ inputProps: { "data-testid": "button-status" } }}
              >
                {Object.entries(labels ?? {}).map(([value, label]) => (
                  <MenuItem key={value} value={value}>
                    {label}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                size="small"
                label={
                  adding.status === ARCHIVED_STATUS
                    ? "Reason"
                    : "Reason (optional)"
                }
                value={adding.reason}
                onChange={(e) => set({ reason: e.target.value })}
                sx={{ flex: 1 }}
                inputProps={{ "data-testid": "button-reason" }}
              />
            </Stack>
          )}
          {adding.kind === "add_tag" && (
            <TextField
              size="small"
              label="Tag"
              value={adding.tag}
              onChange={(e) => set({ tag: e.target.value })}
              inputProps={{ "data-testid": "button-tag" }}
            />
          )}
          {problems.map((p) => (
            <Typography key={p} typography="s3" color="error.main">
              {p}
            </Typography>
          ))}
          <Stack direction="row" gap={1} justifyContent="flex-end">
            <Button
              size="small"
              color="inherit"
              onClick={() => setAdding(null)}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="outlined"
              disabled={problems.length > 0}
              onClick={add}
              data-testid="button-add-confirm"
            >
              Add button
            </Button>
          </Stack>
        </Stack>
      ) : (
        <Button
          size="small"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() => setAdding(blank())}
          disabled={!offered.length}
          data-testid="button-add"
          sx={{ mt: 0.5 }}
        >
          Add a button
        </Button>
      )}
    </Box>
  );
};

ButtonsEditor.propTypes = {
  draft: PropTypes.object.isRequired,
  columns: PropTypes.array.isRequired,
  onDraft: PropTypes.func.isRequired,
  kinds: PropTypes.arrayOf(PropTypes.string),
  statuses: PropTypes.object,
};

export default ButtonsEditor;
