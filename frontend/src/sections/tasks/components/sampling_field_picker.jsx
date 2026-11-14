import PropTypes from "prop-types";
import { forwardRef, useId, useMemo, useRef, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  FormHelperText,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
  autocompleteClasses,
} from "@mui/material";
import { useExactTraceAttributeProperties } from "src/sections/projects/LLMTracing/useExactTraceAttributeProperties";
import { useDashboardFilterValues } from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";
import { FILTER_VALUE_SEARCH_DEBOUNCE_MS } from "src/config/runtime_limits";
import {
  getAttributeLookupMessage,
  getFilterValueReadMessage,
} from "src/utils/queryReadState";
import { samplingMatcherValue } from "./simpleSamplingPolicy";

const sourceLabels = {
  span: "Span attribute",
  metadata: "Metadata",
  resource: "Resource attribute",
};
const keyPlaceholders = {
  span: "Search span attributes or type a key",
  metadata: "Type the exact metadata key",
  resource: "Type the exact resource attribute key",
};
// Shorter hints for a placeholder that first names what an empty field means.
const keyHints = {
  span: "search or type a key",
  metadata: "type the exact key",
  resource: "type the exact key",
};
const SCALAR_FIELD_NOTE =
  "Sampling matches only scalar string, number and boolean fields. Arrays and objects are unavailable.";
const DUPLICATE_GROUPS_MESSAGE =
  "Some values match the same group. Keep one value per group before setting counts or shares.";
const valueOf = (option) =>
  option && typeof option === "object" && Object.hasOwn(option, "value")
    ? option.value
    : option;
const valueIdentity = (value) => `${typeof value}:${JSON.stringify(value)}`;
const displayValue = (value) =>
  value && typeof value === "object" ? JSON.stringify(value) : String(value);
const scalarTypes = new Set(["string", "number", "boolean"]);
const fieldTypes = (field) =>
  field.attributeTypes?.length ? field.attributeTypes : [field.type];
const isScalarField = (field) =>
  fieldTypes(field).every((type) => scalarTypes.has(type));
// Catalog `type` describes the attribute, not just the returned member. An
// array can suggest a scalar member that sampling's JSON_VALUE cannot match.
const scalarValueOption = (option) => {
  const value = valueOf(option);
  const type = option?.type ?? typeof value;
  if (!scalarTypes.has(type)) return [];
  let raw = value;
  if (type === "number" && typeof value === "string" && value.trim())
    raw = Number(value);
  if (type === "boolean" && (value === "true" || value === "false"))
    raw = value === "true";
  return typeof raw === type && (type !== "number" || Number.isFinite(raw))
    ? [{ raw, type }]
    : [];
};
// Wrap selected JSON values so a saved object containing its own `value` key
// is never confused with a discovery-endpoint option envelope.
const wrap = (raw) => ({ raw, type: typeof raw });
const rawOf = (option) => (typeof option === "string" ? option : option.raw);
const typeLabel = (type) => (type === "string" ? "text" : type);
// In a per-group editor, name the type only where a value could pass for
// another type ("42" text beside the number 42), not on every plain word.
const ambiguousType = (raw) =>
  typeof raw !== "string" ||
  !raw.trim() ||
  ["true", "false", "null"].includes(raw.trim()) ||
  Number.isFinite(Number(raw));
const chipValue = (raw) => (raw === "" ? '""' : displayValue(raw));
// MUI pads a small Autocomplete taller than the theme's small fields. Use the
// theme's small-input vertical padding so these pickers line up with the
// selects and number fields beside them.
const compactAutocompleteSx = {
  "& .MuiAutocomplete-inputRoot.MuiInputBase-sizeSmall": {
    py: 0,
    pl: 0.75,
    "& .MuiAutocomplete-input": { py: 0.5, pl: 0.5 },
    "& .MuiAutocomplete-tag": { my: 0.125 },
  },
};
const isInert = (option) =>
  typeof option === "object" && Boolean(option.disabled);
// Until a typed search has been looked up, the loaded rows answer the previous
// search. Keep only those that contain the typed text, so no row reads as a
// match for text it does not contain.
const containsTyped = (typed) => (text) =>
  String(text).toLowerCase().includes(typed.toLowerCase());
// Theme menu rows are 30px with a 1px gap. A long list stops halfway through
// a row so it reads as scrollable; a short one shows every row, so the
// trailing "Load more" row is never the one cut in half.
const ROW_PITCH = 31;
const FULL_LIST_ROWS = 12;
const listboxProps = (count) => ({
  sx: {
    maxHeight:
      count > FULL_LIST_ROWS ? `min(40vh, ${ROW_PITCH * 11 + 15}px)` : "none",
  },
});

// The popup surface for both inline pickers: status lines above the
// suggestions. They are announced (role="status"), unlike disabled rows.
// Pressing on them must not blur the input, or the popup would close before
// the message can be read. MUI shows no popup for an empty free-text list, so
// the pickers raise `loading` to keep a status-only popup open; its own
// loading row stays hidden because the loading copy is a status line here.
const SuggestionsPaper = forwardRef(function SuggestionsPaper(
  { header, listed = false, children, ...props },
  ref,
) {
  return (
    <Paper
      ref={ref}
      {...props}
      sx={{ [`& .${autocompleteClasses.loading}`]: { display: "none" } }}
    >
      {header && (
        <Box
          onMouseDown={(event) => event.preventDefault()}
          sx={{
            px: 1,
            py: 0.75,
            ...(listed && {
              mb: 0.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }),
          }}
        >
          {header}
        </Box>
      )}
      {children}
    </Paper>
  );
});
SuggestionsPaper.propTypes = {
  header: PropTypes.node,
  listed: PropTypes.bool,
  children: PropTypes.node,
};

function suggestionsHeader({ warning, status }) {
  if (!warning && !status.length) return null;
  return (
    <Stack spacing={0.5}>
      {warning && (
        <Alert severity="warning" sx={{ py: 0 }}>
          {warning}
        </Alert>
      )}
      {status.map((line) => (
        <Typography
          key={line}
          role="status"
          variant="s3"
          color="text.secondary"
        >
          {line}
        </Typography>
      ))}
    </Stack>
  );
}

// One row of either picker. Choices go through MUI's selection; disabled rows
// never do (MUI's option click has no disabled guard of its own) but stay
// reachable by keyboard so their reason is read; actions such as "Load more"
// run in place so the list stays open.
function renderSuggestion(props, option) {
  const { key, onClick, ...rest } = props;
  const action = option.kind === "action";
  const activate = action
    ? () => {
        if (!option.disabled) option.run();
      }
    : isInert(option)
      ? undefined
      : onClick;
  return (
    <Box
      component="li"
      key={key}
      {...rest}
      onClick={activate}
      aria-describedby={option.describedBy}
      sx={{
        gap: 1.5,
        ...(action && {
          color: "text.secondary",
          fontWeight: 500,
          borderTop: "1px solid",
          borderColor: "divider",
        }),
      }}
    >
      <Box
        component="span"
        sx={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}
      >
        {option.label}
      </Box>
      {option.meta && (
        <>
          {" "}
          <Typography
            component="span"
            variant="s3"
            color="text.secondary"
            sx={{ flexShrink: 0, textAlign: "right" }}
          >
            {option.meta}
          </Typography>
        </>
      )}
    </Box>
  );
}

// Load more / Retry rows are options so the keyboard reaches them, but Enter
// on one runs it instead of selecting it.
function useSuggestionActions() {
  const highlighted = useRef(null);
  return {
    onHighlightChange: (_, option) => {
      highlighted.current = option;
    },
    onKeyDown: (event) => {
      const option = highlighted.current;
      if (event.key !== "Enter" || option?.kind !== "action") return;
      event.defaultMuiPrevented = true;
      event.preventDefault();
      if (!option.disabled) option.run();
    },
  };
}

const run = (request) => () => request?.()?.catch?.(() => undefined);

// Callers rebuild the values array on every render. MUI clears the typed
// search whenever its `value` changes identity, so keep the caller's first
// array until the list's contents actually change.
function useStableList(list) {
  const identity = list.map(valueIdentity).join("\n");
  const stable = useRef({ identity, list });
  if (stable.current.identity !== identity) stable.current = { identity, list };
  return stable.current.list;
}

export function SamplingFieldPicker({
  project,
  value,
  onChange,
  label = "Group field",
  sources = ["span", "metadata", "resource"],
  helperText,
  emptyLabel,
}) {
  const helperId = useId();
  const typeHelpId = useId();
  const committedSource = value?.source || "span";
  const key = value?.key || "";
  // A location picked before any key is typed is a draft: the policy has no
  // field to carry it until a key is chosen.
  const [draftSource, setDraftSource] = useState(committedSource);
  const source = key ? committedSource : draftSource;
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState(false);
  const actions = useSuggestionActions();
  // MUI resets the typed text whenever `value` changes identity, so the
  // selection object must only change when the field does.
  const selected = useMemo(
    () => (key ? { kind: "field", source: committedSource, key } : null),
    [committedSource, key],
  );
  const span = source === "span";
  const typed = query.trim();
  const inventory = useExactTraceAttributeProperties({
    projectId: project,
    source: "spans",
    search: query,
    enabled: Boolean(project) && opened && span,
  });
  const message = span
    ? getAttributeLookupMessage(inventory.queryReadState)
    : null;
  const lookupFailed =
    span && Boolean(inventory.isError || inventory.exactSearchError);
  const catalog = span ? inventory.data || [] : [];
  const commit = (next) => {
    if (next.source === committedSource && next.key === key) return;
    onChange(next);
  };
  // A key typed into an empty field is kept when focus leaves, as the old
  // manual-entry box did. An empty Custom ID means "use the project's contact
  // attribute", so dropping the text would silently sample by another field.
  // Once a field is chosen, typing is a search and leaving restores it.
  const commitTyped = () => {
    if (key || !typed) return;
    setDraftSource(source);
    commit({ source, key: query });
  };

  const searchSettled = (inventory.debouncedSearch ?? typed) === typed;
  const matchesTyped = containsTyped(typed);
  const shownCatalog = searchSettled
    ? catalog
    : catalog.filter(
        (field) =>
          matchesTyped(field.name || field.id) || matchesTyped(field.id),
      );
  const fields = shownCatalog.map((field) => {
    const scalar = isScalarField(field);
    const types = fieldTypes(field).join(", ");
    return {
      id: `field:${field.id}`,
      kind: "field",
      source: "span",
      key: field.id,
      label: field.name || field.id,
      meta: scalar ? types : `${types} · unavailable`,
      disabled: !scalar,
      describedBy: scalar ? undefined : typeHelpId,
    };
  });
  // Usable rows first: the first row is highlighted as you type, and Enter
  // on an unavailable one does nothing.
  const options = fields.filter((option) => !option.disabled);
  if (typed && query !== key && !catalog.some((f) => f.id === query))
    options.push({
      id: `manual:${query}`,
      kind: "manual",
      source,
      key: query,
      label: `Use “${query}”`,
      meta: sourceLabels[source],
    });
  options.push(...fields.filter((option) => option.disabled));
  if (lookupFailed)
    options.push({
      id: "action:retry",
      kind: "action",
      label: "Retry fields",
      disabled: inventory.isFetching,
      run: run(inventory.refetch),
    });
  // "Load more" would page the previous search, so it waits for this one.
  if (span && inventory.hasNextPage && searchSettled)
    options.push({
      id: "action:more",
      kind: "action",
      label: inventory.isFetchingNextPage
        ? "Loading more fields…"
        : inventory.isFetchNextPageError
          ? "Retry more fields"
          : "Load more fields",
      disabled: inventory.isFetching,
      run: run(inventory.fetchNextPage),
    });

  const busy = inventory.isFetching && !inventory.isFetchingNextPage;
  const status = [];
  if (span && !lookupFailed) {
    if (inventory.isLoading) status.push("Loading fields…");
    else if (typed && (!searchSettled || busy))
      status.push("Searching span attributes…");
    else if (inventory.isSuccess && !busy && !catalog.length)
      status.push(
        typed
          ? "No matching span attributes in the loaded list."
          : "No span attributes found in the loaded list. Type an exact key to use it.",
      );
  }
  if (!span && !typed)
    status.push(
      `Suggestions list span attributes only. ${keyPlaceholders[source]}.`,
    );
  if (message) status.push(message);
  const header = suggestionsHeader({
    warning: lookupFailed
      ? "Could not load span attributes. Retry, or type the exact key."
      : null,
    status,
  });
  const placeholder =
    !key && emptyLabel
      ? `${emptyLabel} · ${keyHints[source]}`
      : keyPlaceholders[source];

  return (
    <Stack spacing={0.75} sx={{ minWidth: 0, flex: 1 }}>
      <Stack direction="row" spacing={1} alignItems="flex-end">
        {sources.length > 1 && (
          <TextField
            select
            size="small"
            label="Location"
            value={source}
            disabled={!project}
            onChange={(event) => {
              setDraftSource(event.target.value);
              setQuery("");
              if (key) commit({ source: event.target.value, key });
            }}
            SelectProps={{
              SelectDisplayProps: {
                "aria-label": `${label} location`,
                "aria-labelledby": undefined,
              },
            }}
            sx={{ width: 176, flexShrink: 0 }}
          >
            {sources.map((option) => (
              <MenuItem key={option} value={option}>
                {sourceLabels[option]}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Autocomplete
          freeSolo
          forcePopupIcon
          autoHighlight
          clearOnBlur
          selectOnFocus
          handleHomeEndKeys
          disabledItemsFocusable
          size="small"
          sx={{ ...compactAutocompleteSx, flex: 1, minWidth: 0 }}
          disabled={!project}
          options={options}
          value={selected}
          filterOptions={(items) => items}
          getOptionKey={(option) =>
            typeof option === "string" ? option : option.id
          }
          getOptionLabel={(option) =>
            typeof option === "string" ? option : option.key ?? option.label
          }
          isOptionEqualToValue={(option, current) =>
            typeof option === "object" &&
            (option.kind === "field" || option.kind === "manual") &&
            option.key === current.key &&
            option.source === current.source
          }
          getOptionDisabled={isInert}
          loading={!options.length && status.length > 0}
          onOpen={() => setOpened(true)}
          onInputChange={(_, next, reason) =>
            setQuery(reason === "input" ? next : "")
          }
          onChange={(event, next, reason) => {
            // Emptying the text box is a new search, not a request to drop
            // the field; the clear button (a click) is.
            if (reason === "clear" && event?.type === "change") return;
            if (next == null) {
              setDraftSource(source);
              commit({ source, key: "" });
            } else if (typeof next === "string") {
              if (next.trim()) commit({ source, key: next });
            } else if (next.kind === "field" || next.kind === "manual") {
              setDraftSource(next.source);
              commit({ source: next.source, key: next.key });
            }
          }}
          onHighlightChange={actions.onHighlightChange}
          onKeyDown={actions.onKeyDown}
          renderOption={renderSuggestion}
          PaperComponent={SuggestionsPaper}
          ListboxProps={listboxProps(options.length)}
          slotProps={{ paper: { header, listed: options.length > 0 } }}
          renderInput={(params) => (
            <TextField
              {...params}
              label={label}
              placeholder={placeholder}
              inputProps={{
                ...params.inputProps,
                "aria-describedby": helperText
                  ? `${helperId} ${typeHelpId}`
                  : typeHelpId,
                onBlur: (event) => {
                  commitTyped();
                  params.inputProps.onBlur?.(event);
                },
              }}
            />
          )}
        />
      </Stack>
      <FormHelperText sx={{ mt: 0 }}>
        {helperText && (
          <>
            <span id={helperId}>{helperText}</span>{" "}
          </>
        )}
        <span id={typeHelpId}>{SCALAR_FIELD_NOTE}</span>
      </FormHelperText>
    </Stack>
  );
}
SamplingFieldPicker.propTypes = {
  project: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  label: PropTypes.string,
  sources: PropTypes.array,
  helperText: PropTypes.string,
  // What an empty field means, named in its placeholder.
  emptyLabel: PropTypes.string,
};

export function SamplingValuePicker({
  project,
  predicate,
  onChange,
  distinctGroups = false,
}) {
  const [search, setSearch] = useState("");
  const [opened, setOpened] = useState(false);
  const [rejected, setRejected] = useState(false);
  const actions = useSuggestionActions();
  const settledSearch = useDebounce(search, FILTER_VALUE_SEARCH_DEBOUNCE_MS);
  const typed = search.trim();
  const hasField = Boolean(predicate.key && project);
  // Only span attributes use this existing catalog adapter. Metadata/resource
  // identities are never guessed or flattened into a different backend source.
  const discoverable = predicate.source === "span" && hasField;
  const inventory = useDashboardFilterValues({
    metricName: predicate.key,
    metricType: "custom_attribute",
    projectIds: project ? [project] : [],
    source: "traces",
    search: settledSearch,
    searchGesture: search,
    enabled: discoverable && opened,
  });
  const values = useStableList(predicate.values);
  const selected = useMemo(() => values.map(wrap), [values]);
  const selectedIds = new Set(selected.map((item) => valueIdentity(item.raw)));
  const groups = new Map(
    distinctGroups
      ? selected.map((item) => [samplingMatcherValue(item.raw), item.raw])
      : [],
  );
  // In a per-group editor two values that match the same rows would make two
  // overlapping groups, so a value that shares a group with a chosen one is
  // offered but cannot be added.
  const sameGroupAs = (raw) => {
    if (!groups.has(samplingMatcherValue(raw))) return null;
    const chosen = groups.get(samplingMatcherValue(raw));
    return valueIdentity(chosen) === valueIdentity(raw) ? null : chosen;
  };
  const suggestions = discoverable
    ? (inventory.data || []).flatMap(scalarValueOption)
    : [];
  const searchSettled = typed === settledSearch.trim();
  const matchesTyped = containsTyped(typed);
  // Chosen values are chips, not rows.
  const options = suggestions
    .filter(({ raw }) => searchSettled || matchesTyped(displayValue(raw)))
    .filter(({ raw }) => !selectedIds.has(valueIdentity(raw)))
    .map(({ raw, type }) => {
      const twin = sameGroupAs(raw);
      return {
        id: `value:${valueIdentity(raw)}`,
        kind: "value",
        raw,
        type,
        label: displayValue(raw),
        meta:
          twin === null
            ? type
            : `${type} · same group as ${displayValue(twin)}`,
        disabled: twin !== null,
      };
    });
  if (
    typed &&
    !selectedIds.has(valueIdentity(search)) &&
    !suggestions.some(({ raw }) => valueIdentity(raw) === valueIdentity(search))
  ) {
    const twin = sameGroupAs(search);
    options.push({
      id: `manual:${search}`,
      kind: "manual",
      raw: search,
      type: "string",
      label: `Use “${search}”`,
      meta:
        twin === null
          ? "string"
          : `string · same group as ${displayValue(twin)}`,
      disabled: twin !== null,
    });
  }
  const loadFailed = discoverable && inventory.isError;
  if (loadFailed)
    options.push({
      id: "action:retry",
      kind: "action",
      label: "Retry values",
      disabled: inventory.isFetching,
      run: run(inventory.refetch),
    });
  if (discoverable && inventory.hasNextPage && searchSettled)
    options.push({
      id: "action:more",
      kind: "action",
      label: inventory.isFetchingNextPage
        ? "Loading more values…"
        : inventory.isFetchNextPageError
          ? "Retry more values"
          : "Load more values",
      disabled: inventory.isFetching,
      run: run(inventory.fetchNextPage),
    });

  // Until a typed search has been looked up, a lone "Use" row must not read
  // as "this value does not exist".
  const busy = inventory.isFetching && !inventory.isFetchingNextPage;
  const status = [];
  if (discoverable && !loadFailed) {
    if (typed && (!searchSettled || busy)) status.push("Searching values…");
    else if (busy && !suggestions.length) status.push("Loading values…");
    else if (inventory.isFetched && !busy && !suggestions.length)
      status.push(
        typed
          ? "No matching values in the loaded list."
          : "No values found in the loaded list. Type an exact value and press Enter.",
      );
  }
  const message = discoverable
    ? getFilterValueReadMessage(inventory.queryReadState)
    : null;
  if (message) status.push(message);
  const header = suggestionsHeader({
    warning: loadFailed
      ? "Could not load values. Retry or enter exact values manually."
      : null,
    status,
  });
  // Typed text is added only by Enter or by choosing its "Use" row; it stays
  // in the box when focus leaves so it is never dropped unseen.
  const helperText = rejected
    ? DUPLICATE_GROUPS_MESSAGE
    : !hasField
      ? undefined
      : typed
        ? `“${search}” is not added yet. Press Enter to add it as one value.`
        : discoverable
          ? "Pick existing values, or type one exact value and press Enter. Commas remain part of a value."
          : "Value suggestions are not available for this field location. Enter each exact value and press Enter.";

  return (
    <Autocomplete
      multiple
      freeSolo
      forcePopupIcon
      disableCloseOnSelect
      disableClearable
      disabledItemsFocusable
      clearOnBlur={false}
      size="small"
      sx={{ ...compactAutocompleteSx, minWidth: 0 }}
      disabled={!hasField}
      options={options}
      value={selected}
      inputValue={search}
      filterOptions={(items) => items}
      getOptionKey={(option) =>
        typeof option === "string" ? option : option.id
      }
      getOptionLabel={(option) =>
        typeof option === "string"
          ? option
          : option.kind === "manual"
            ? option.raw
            : option.label ?? displayValue(option.raw)
      }
      isOptionEqualToValue={(option, current) =>
        (typeof option === "string" ||
          option.kind === "value" ||
          option.kind === "manual" ||
          option.kind === undefined) &&
        valueIdentity(rawOf(option)) === valueIdentity(rawOf(current))
      }
      getOptionDisabled={isInert}
      loading={!options.length && status.length > 0}
      onOpen={() => setOpened(true)}
      onInputChange={(_, next, reason) => {
        if (reason === "input") setRejected(false);
        setSearch(next);
      }}
      onChange={(_, next) => {
        const raws = next.map(rawOf);
        if (
          distinctGroups &&
          new Set(raws.map(samplingMatcherValue)).size !== raws.length
        ) {
          // MUI has already cleared the box; keep the rejected text so it
          // can be corrected instead of retyped.
          setRejected(true);
          setSearch(search);
          return;
        }
        setRejected(false);
        onChange(raws);
      }}
      onHighlightChange={actions.onHighlightChange}
      onKeyDown={actions.onKeyDown}
      renderOption={renderSuggestion}
      renderTags={(items, getTagProps) =>
        items.map((option, index) => {
          const { key, ...tagProps } = getTagProps({ index });
          return (
            <Chip
              key={`${key}:${valueIdentity(option.raw)}`}
              {...tagProps}
              size="small"
              label={`${chipValue(option.raw)}${distinctGroups && ambiguousType(option.raw) ? ` (${typeLabel(option.type)})` : ""}`}
            />
          );
        })
      }
      PaperComponent={SuggestionsPaper}
      ListboxProps={listboxProps(options.length)}
      slotProps={{ paper: { header, listed: options.length > 0 } }}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Group values"
          placeholder={
            selected.length
              ? undefined
              : hasField
                ? "Search values or type one"
                : "Choose a group field first"
          }
          error={rejected}
          helperText={helperText}
        />
      )}
    />
  );
}
SamplingValuePicker.propTypes = {
  project: PropTypes.string,
  predicate: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  distinctGroups: PropTypes.bool,
};
