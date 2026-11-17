// The structured filter popover (W9-SCOPE §5 A2): `Filter` opens a panel
// titled by the page (`Filter datasets`) with rows `Where [field ▾]
// [operator ▾] [value]`, `+ Add a condition` and `Clear all`. Conditions
// apply as soon as they are complete; an incomplete row shows its
// sentence and is not sent. Value menus show facet counts. Generic over
// the page's fields (listFilterModel.js).
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Badge,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  IconButton,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  conditionProblem,
  emptyValueFor,
  fieldById,
  isListOp,
  isNoValueOp,
  isRangeOp,
  newCondition,
  opsFor,
  opWord,
  replaceAt,
  removeAt,
  serializeFilters,
} from "./listFilterModel";

const toInput = (field, v) => {
  if (v === null || v === undefined || v === "") return "";
  if (field.type === "percent")
    return String(Number((Number(v) * 100).toFixed(4)));
  if (field.type === "datetime") return String(v).slice(0, 10);
  return String(v);
};
const fromInput = (field, text) => {
  if (text === "") return null;
  if (field.type === "percent") return Number(text) / 100;
  if (["number", "points"].includes(field.type)) return Number(text);
  return text;
};

function ScalarInput({ field, value, onChange, label }) {
  const numeric = ["number", "percent", "points"].includes(field.type);
  return (
    <TextField
      size="small"
      value={toInput(field, value)}
      onChange={(e) => onChange(fromInput(field, e.target.value))}
      type={numeric ? "number" : field.type === "datetime" ? "date" : "text"}
      inputProps={{ "aria-label": label }}
      InputProps={
        field.type === "percent"
          ? { endAdornment: <Typography typography="s2">%</Typography> }
          : field.type === "points"
            ? { endAdornment: <Typography typography="s2">pts</Typography> }
            : undefined
      }
      sx={{ width: field.type === "datetime" ? 160 : 140 }}
    />
  );
}
ScalarInput.propTypes = {
  field: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
};

function ValueInput({ field, cond, facets, labelOf, onValue }) {
  const op = cond.filter_config.filter_op;
  const v = cond.filter_config.filter_value;
  if (isNoValueOp(op)) return null;
  if (isListOp(op)) {
    const options = facets?.[field.id] ?? [];
    return (
      <Autocomplete
        multiple
        size="small"
        freeSolo={!options.length}
        options={options.map((o) => String(o.value))}
        value={(Array.isArray(v) ? v : []).map(String)}
        onChange={(_, next) => onValue(next)}
        getOptionLabel={(o) =>
          (labelOf && labelOf(field, o)) ??
          options.find((x) => String(x.value) === o)?.label ??
          o
        }
        renderOption={(props, o) => {
          const f = options.find((x) => String(x.value) === o);
          return (
            <li {...props} key={o}>
              <Box sx={{ flex: 1, minWidth: 0 }}>{f?.label ?? o}</Box>
              {f ? (
                <Typography typography="s3" color="text.secondary">
                  {f.count}
                </Typography>
              ) : null}
            </li>
          );
        }}
        renderInput={(params) => (
          <TextField
            {...params}
            placeholder={v?.length ? "" : "Pick values"}
            inputProps={{
              ...params.inputProps,
              "aria-label": `${field.label} values`,
            }}
          />
        )}
        sx={{ minWidth: 220, flex: 1 }}
      />
    );
  }
  if (isRangeOp(op)) {
    const [lo, hi] = Array.isArray(v) ? v : [null, null];
    return (
      <Stack direction="row" alignItems="center" gap={0.5}>
        <ScalarInput
          field={field}
          value={lo}
          onChange={(x) => onValue([x, hi])}
          label={`${field.label} from`}
        />
        <Typography typography="s2">and</Typography>
        <ScalarInput
          field={field}
          value={hi}
          onChange={(x) => onValue([lo, x])}
          label={`${field.label} to`}
        />
      </Stack>
    );
  }
  if (field.type === "boolean")
    return (
      <TextField
        select
        size="small"
        value={v === false ? "false" : "true"}
        onChange={(e) => onValue(e.target.value === "true")}
        inputProps={{ "aria-label": `${field.label} value` }}
        sx={{ width: 110 }}
      >
        <MenuItem value="true">true</MenuItem>
        <MenuItem value="false">false</MenuItem>
      </TextField>
    );
  return (
    <ScalarInput
      field={field}
      value={v}
      onChange={onValue}
      label={`${field.label} value`}
    />
  );
}
ValueInput.propTypes = {
  field: PropTypes.object.isRequired,
  cond: PropTypes.object.isRequired,
  facets: PropTypes.object,
  labelOf: PropTypes.func,
  onValue: PropTypes.func.isRequired,
};

/** Only complete conditions are sent. */
const complete = (list, fields) =>
  list.filter((c) => !conditionProblem(c, fields));

export default function ListFilterButton({
  fields,
  filters,
  onChange,
  facets,
  labelOf,
  title = "Filter",
}) {
  const [anchor, setAnchor] = useState(null);
  const [draft, setDraft] = useState(filters ?? []);
  const applied = useMemo(() => serializeFilters(filters), [filters]);
  // The page's filters change from outside (a chip removed, a saved view).
  useEffect(() => {
    if (serializeFilters(complete(draft, fields)) !== applied)
      setDraft(filters ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applied]);
  const commit = (next) => {
    setDraft(next);
    const ok = complete(next, fields);
    if (serializeFilters(ok) !== applied) onChange(ok);
  };
  const setField = (i, id) =>
    commit(replaceAt(draft, i, newCondition(fieldById(fields, id))));
  const setOp = (i, op) => {
    const c = draft[i];
    const field = fieldById(fields, c.column_id);
    commit(
      replaceAt(draft, i, {
        ...c,
        filter_config: {
          ...c.filter_config,
          filter_op: op,
          filter_value: emptyValueFor(field, op),
        },
      }),
    );
  };
  const setValue = (i, value) =>
    commit(
      replaceAt(draft, i, {
        ...draft[i],
        filter_config: { ...draft[i].filter_config, filter_value: value },
      }),
    );
  const setSignificant = (i, on) =>
    commit(
      replaceAt(draft, i, {
        ...draft[i],
        filter_config: {
          ...draft[i].filter_config,
          significant: on || undefined,
        },
      }),
    );
  const count = filters?.length ?? 0;
  return (
    <>
      <Badge badgeContent={count} color="primary" invisible={!count}>
        <Button
          size="small"
          color="inherit"
          startIcon={<Iconify icon="mdi:filter-variant" width={16} />}
          onClick={(e) => {
            setDraft(filters ?? []);
            setAnchor(e.currentTarget);
          }}
          aria-haspopup="dialog"
          // The list toolbar's text-button look (as View and Group by).
          sx={{
            typography: "s2",
            color: count ? "text.primary" : "text.secondary",
            minWidth: 0,
          }}
        >
          Filter
        </Button>
      </Badge>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 2, width: 640, maxWidth: "95vw" } } }}
      >
        <Box role="dialog" aria-label={title}>
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            sx={{ mb: 1.5 }}
          >
            {title}
          </Typography>
          {!draft.length && (
            <Typography typography="s2" color="text.secondary" sx={{ mb: 1 }}>
              No conditions yet.
            </Typography>
          )}
          <Stack gap={1}>
            {draft.map((c, i) => {
              const field = fieldById(fields, c.column_id);
              const problem = conditionProblem(c, fields);
              return (
                <Box key={i} data-testid="filter-row">
                  <Stack direction="row" alignItems="center" gap={1}>
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      sx={{ width: 44 }}
                    >
                      {i === 0 ? "Where" : "and"}
                    </Typography>
                    <TextField
                      select
                      size="small"
                      value={c.column_id}
                      onChange={(e) => setField(i, e.target.value)}
                      inputProps={{ "aria-label": "Field" }}
                      sx={{ width: 170 }}
                    >
                      {fields.map((f) => (
                        <MenuItem key={f.id} value={f.id}>
                          {f.label}
                        </MenuItem>
                      ))}
                    </TextField>
                    {field && (
                      <TextField
                        select
                        size="small"
                        value={c.filter_config.filter_op}
                        onChange={(e) => setOp(i, e.target.value)}
                        inputProps={{ "aria-label": "Operator" }}
                        sx={{ width: 140 }}
                      >
                        {opsFor(field).map((op) => (
                          <MenuItem key={op} value={op}>
                            {opWord(field, op)}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                    {field && (
                      <ValueInput
                        field={field}
                        cond={c}
                        facets={facets}
                        labelOf={labelOf}
                        onValue={(v) => setValue(i, v)}
                      />
                    )}
                    <IconButton
                      size="small"
                      aria-label="Remove condition"
                      onClick={() => commit(removeAt(draft, i))}
                    >
                      <Iconify icon="mdi:close" width={16} />
                    </IconButton>
                  </Stack>
                  {field?.significant && (
                    <FormControlLabel
                      sx={{ ml: 6.5 }}
                      control={
                        <Checkbox
                          size="small"
                          checked={Boolean(c.filter_config.significant)}
                          onChange={(e) => setSignificant(i, e.target.checked)}
                        />
                      }
                      label="Significant only"
                      componentsProps={{ typography: { typography: "s2" } }}
                    />
                  )}
                  {problem && (
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ ml: 6.5, mt: 0.25 }}
                    >
                      {problem}
                    </Typography>
                  )}
                </Box>
              );
            })}
          </Stack>
          <Stack
            direction="row"
            justifyContent="space-between"
            sx={{ mt: 1.5 }}
          >
            <Button
              size="small"
              startIcon={<Iconify icon="mdi:plus" width={16} />}
              onClick={() => setDraft([...draft, newCondition(fields[0])])}
            >
              Add a condition
            </Button>
            <Button
              size="small"
              color="inherit"
              disabled={!draft.length}
              onClick={() => commit([])}
            >
              Clear all
            </Button>
          </Stack>
        </Box>
      </Popover>
    </>
  );
}

ListFilterButton.propTypes = {
  fields: PropTypes.array.isRequired,
  filters: PropTypes.array,
  onChange: PropTypes.func.isRequired,
  facets: PropTypes.object,
  labelOf: PropTypes.func,
  title: PropTypes.string,
};
