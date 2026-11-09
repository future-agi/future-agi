import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  FormControlLabel,
  IconButton,
  Paper,
  Popover,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import useMediaQuery from "@mui/material/useMediaQuery";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { DENSITIES } from "../DensityMenu";
import {
  SavedViewLabel,
  SaveViewDialog,
  canDeleteView,
  useSavedViewsModel,
} from "../SavedViewsMenu";
import { ColumnsPanel } from "./ColumnsMenu";
import { registerColumnsHost } from "./columnsMenuHost";
import { useLayoutPrefs } from "./layoutPrefs";
import { useSections } from "./Section";

const SHORT_DENSITY = {
  compact: "Compact",
  comfortable: "Comfortable",
  tall: "Tall",
  full: "Full",
};

const VIEW_TOOLTIP =
  "View options: density, columns, what this page shows, saved views";
const MODIFIED_TOOLTIP = "Reset view is in this menu";

/** How many column choices differ from the default (hidden, frozen, order…). */
const prefChanges = (p) =>
  Math.max(
    1,
    Object.values(p ?? {}).reduce(
      (n, v) =>
        n +
        (Array.isArray(v)
          ? 1
          : v && typeof v === "object"
            ? Object.keys(v).length
            : 1),
      0,
    ),
  );

function Heading({ children }) {
  return (
    <Typography
      typography="s3"
      color="text.secondary"
      fontWeight="fontWeightSemiBold"
      sx={{ px: 1.5, pt: 1.25, pb: 0.5 }}
    >
      {children}
    </Typography>
  );
}
Heading.propTypes = { children: PropTypes.node };

/**
 * IT-R4 G6 (S6, additive): radio groups (`choices`: Color / Group / Sort …)
 * and one-click `actions` (Row checks …) at the top of the View panel.
 */
function ChoicesBlock({ choices, actions, onDone }) {
  if (!choices?.length && !actions?.length) return null;
  return (
    <>
      {(choices ?? []).map((c) => (
        <Box key={`c-${c.id}`} data-testid={`view-choice-${c.id}`}>
          <Heading>{c.label}</Heading>
          <Stack
            role="radiogroup"
            aria-label={c.label}
            direction="row"
            flexWrap="wrap"
            gap={0.5}
            sx={{ px: 1.5, pb: 1 }}
          >
            {(c.options ?? []).map((o) => {
              const on = o.value === c.value;
              return (
                <Button
                  key={String(o.value)}
                  role="radio"
                  aria-checked={on}
                  size="small"
                  variant={on ? "contained" : "outlined"}
                  color={on ? "primary" : "inherit"}
                  disabled={o.disabled}
                  onClick={() => c.onChange?.(o.value)}
                  sx={{
                    typography: "s3",
                    height: 24,
                    minWidth: 0,
                    px: 1,
                    textTransform: "none",
                    ...(on ? {} : { borderColor: "divider" }),
                  }}
                >
                  {o.label}
                </Button>
              );
            })}
          </Stack>
        </Box>
      ))}
      {actions?.length ? (
        <Stack sx={{ px: 0.75, pb: 0.75 }}>
          {actions.map((a) => (
            <Button
              key={`a-${a.id}`}
              size="small"
              color="inherit"
              data-testid={`view-action-${a.id}`}
              startIcon={a.icon ? <Iconify icon={a.icon} width={16} /> : null}
              onClick={(e) => {
                onDone?.();
                a.onClick?.(e);
              }}
              sx={{ justifyContent: "flex-start", typography: "s2" }}
            >
              {a.label}
            </Button>
          ))}
        </Stack>
      ) : null}
      <Divider />
    </>
  );
}
ChoicesBlock.propTypes = {
  choices: PropTypes.array,
  actions: PropTypes.array,
  onDone: PropTypes.func,
};

/**
 * ViewMenu — one `View` button instead of four icons (ONE-VIEW-SPEC §3.7,
 * Linear's Display pattern). Popover 300 px, in order: Density · Columns
 * (`7 of 12 shown · Edit…`) · Show on this page (a checkbox per collapsible
 * Section of this surface + `extras`) · Saved views (`Save as…`, `Copy link
 * to this view`) · footer `Reset view`. An amber dot says the view changed.
 * ⌘K: `View options…`, `Reset view`, `Save view as…`.
 *
 * Saved views store the layout of this surface (sections, density, extras)
 * and the columns' prefs under `layout`, so `Save as…` captures hidden /
 * frozen / ordered columns and open sections; applying one restores them.
 *
 * Props:
 * @param {string} props.surface
 * @param {string} [props.scopeId]
 * @param {{value: string, onChange: Function}} [props.density]
 * @param {object} [props.columns]  useColumnLayout result
 * @param {{id: string, label: string}[]} [props.groups]  group headings of
 *   the Columns panel (else the layout's own `groups`)
 * @param {boolean} [props.sections=true]  list this surface's Sections
 * @param {{state: object, onApply: Function, canShare?: boolean}} [props.savedViews]
 *   the page's URL view state (useViewUrlState) and how to apply one;
 *   `canShare` (W9 A0) offers "Share with everyone in this workspace"
 *   (false: shown disabled, for a viewer)
 * @param {{id, label, checked, onChange, description?}[]} [props.extras]
 * @param {boolean} [props.isModified]  the page's own state differs (URL
 *   filters etc.); layout changes are detected here
 * @param {Function} [props.onReset]  reset the page's own state
 * @param {boolean} [props.iconOnly]  force icon-only (default: below 1366 px)
 * @param {boolean} [props.inline]  gallery only: the panel renders open,
 *   inline under the button
 */
export default function ViewMenu({
  surface,
  scopeId,
  density,
  columns,
  groups,
  sections = true,
  savedViews,
  extras,
  isModified = false,
  onReset,
  iconOnly,
  inline = false,
  choices,
  actions,
}) {
  const btnRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [hiddenOnly, setHiddenOnly] = useState(false);
  const [naming, setNaming] = useState(false);
  const narrow = useMediaQuery("(max-width:1365.95px)", { noSsr: true });
  const compact = iconOnly ?? narrow;
  const [prefs, patch, resetLayout] = useLayoutPrefs(surface, scopeId);
  const sec = useSections(surface, scopeId);
  const shownSections = sections ? sec.sections : [];

  const layoutModified =
    (columns ? !columns.isDefault : false) ||
    Object.keys(prefs.sections ?? {}).length > 0 ||
    prefs.density != null ||
    Object.keys(prefs.extras ?? {}).length > 0;
  const modified = Boolean(isModified) || layoutModified;
  // IT-R1 G5 P7: the amber dot says how much differs (`2 changes from
  // default`), not just that something does.
  const changeCount = modified
    ? Math.max(
        1,
        (columns && !columns.isDefault ? prefChanges(columns.prefs) : 0) +
          Object.keys(prefs.sections ?? {}).length +
          (prefs.density != null ? 1 : 0) +
          Object.keys(prefs.extras ?? {}).length +
          (isModified ? 1 : 0),
      )
    : 0;

  const savedLayout = useMemo(() => {
    const { outline: _o, v: _v, ...rest } = prefs;
    return { ...rest, columns: columns ? columns.prefs : prefs.columns };
  }, [prefs, columns]);
  const model = useSavedViewsModel({
    // No saved views on this page → no request.
    surface: savedViews ? surface : null,
    scopeId,
    state: savedViews?.state ?? {},
    layout: savedLayout,
    onApply: savedViews?.onApply,
    onApplyLayout: (l) => {
      patch((cur) => ({
        ...cur,
        sections: l.sections ?? {},
        density: l.density ?? null,
        extras: l.extras ?? {},
        columns: columns ? cur.columns : l.columns ?? {},
      }));
      if (columns) columns.setPrefs(l.columns ?? {});
    },
    isModified: modified,
  });

  const resetView = () => {
    onReset?.();
    resetLayout(["sections", "density", "extras"]);
    columns?.reset();
  };

  useEffect(() => {
    if (!columns) return undefined;
    return registerColumnsHost(
      columns.key,
      (opts = {}) => {
        setHiddenOnly(Boolean(opts.hiddenOnly));
        setOpen(false);
        setColumnsOpen(true);
      },
      1,
    );
  }, [columns?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  useRegisterCommands(
    `view:${surface}:${scopeId ?? ""}`,
    [
      {
        id: `view.options.${surface}`,
        label: "View options…",
        keywords: ["view", "display", "density", "layout", "options"],
        icon: "mdi:tune-variant",
        run: () => setOpen(true),
      },
      {
        id: `view.reset.${surface}`,
        label: "Reset view",
        keywords: ["default", "restore", "view"],
        icon: "mdi:restore",
        run: resetView,
        when: () => modified,
      },
      ...(savedViews
        ? [
            {
              id: `view.save.${surface}`,
              label: "Save view as…",
              keywords: ["saved view", "bookmark"],
              icon: "mdi:content-save-outline",
              run: () => setNaming(true),
            },
          ]
        : []),
    ],
    [modified, Boolean(savedViews)],
  );

  const shownCount = columns?.visibleIds.length ?? 0;
  const totalCount = columns?.columns.length ?? 0;

  const button = compact ? (
    <IconButton
      ref={btnRef}
      size="small"
      aria-label="View options"
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={() => setOpen(true)}
      sx={{ color: "text.secondary", position: "relative" }}
    >
      <Iconify icon="mdi:tune-variant" width={18} />
      {modified && <Dot sx={{ position: "absolute", top: 4, right: 4 }} />}
    </IconButton>
  ) : (
    <Button
      ref={btnRef}
      size="small"
      variant="text"
      color="inherit"
      aria-label="View options"
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={() => setOpen(true)}
      startIcon={<Iconify icon="mdi:tune-variant" width={16} />}
      sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
    >
      View
      {modified && <Dot sx={{ ml: 0.75 }} />}
    </Button>
  );

  return (
    <>
      <CustomTooltip
        show
        size="small"
        disableInteractive
        title={
          modified
            ? `${changeCount} ${changeCount === 1 ? "change" : "changes"} from default · ${MODIFIED_TOOLTIP}`
            : VIEW_TOOLTIP
        }
      >
        {button}
      </CustomTooltip>
      {inline ? (
        <Paper variant="outlined" sx={{ width: 300, mt: 0.5, borderRadius: 1 }}>
          <Box role="dialog" aria-label="View options" sx={{ pb: 0.5 }}>
            <ChoicesBlock choices={choices} actions={actions} />
            {density && (
              <>
                <Heading>Density</Heading>
                <Box sx={{ px: 1.5, pb: 1 }}>
                  <ToggleButtonGroup
                    exclusive
                    fullWidth
                    size="small"
                    value={density.value}
                    onChange={(_, v) => v && density.onChange(v)}
                    aria-label="Row density"
                  >
                    {DENSITIES.map((d) => (
                      <ToggleButton
                        key={d.value}
                        value={d.value}
                        aria-label={d.label}
                        title={d.label}
                        sx={{
                          typography: "s3",
                          py: 0.5,
                          px: 0.5,
                          textTransform: "none",
                        }}
                      >
                        {SHORT_DENSITY[d.value] ?? d.label}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                </Box>
              </>
            )}
            {columns && (
              <>
                {density && <Divider />}
                <Heading>Columns</Heading>
                <Stack
                  direction="row"
                  alignItems="center"
                  sx={{ px: 1.5, pb: 1 }}
                >
                  <Typography typography="s2" sx={{ flex: 1 }}>
                    {`${shownCount} of ${totalCount} columns shown`}
                  </Typography>
                  <Button
                    size="small"
                    onClick={() => {
                      setHiddenOnly(false);
                      setOpen(false);
                      setColumnsOpen(true);
                    }}
                    sx={{ typography: "s2", minWidth: 0 }}
                  >
                    Edit…
                  </Button>
                </Stack>
              </>
            )}
            {(shownSections.length > 0 || extras?.length > 0) && (
              <>
                <Divider />
                <Heading>Show on this page</Heading>
                <Stack sx={{ px: 1.5, pb: 0.75 }}>
                  {shownSections.map((s) => (
                    <FormControlLabel
                      key={`s-${s.id}`}
                      control={
                        <Checkbox
                          size="small"
                          checked={s.open}
                          onChange={() => sec.setOpen(s.id, !s.open)}
                          sx={{ p: 0.5 }}
                        />
                      }
                      label={s.title}
                      componentsProps={{ typography: { typography: "s2" } }}
                      sx={{ m: 0, gap: 0.5 }}
                    />
                  ))}
                  {(extras ?? []).map((x) => (
                    <FormControlLabel
                      key={`x-${x.id}`}
                      control={
                        <Checkbox
                          size="small"
                          checked={Boolean(x.checked)}
                          onChange={() => x.onChange?.(!x.checked)}
                          sx={{ p: 0.5 }}
                        />
                      }
                      label={
                        x.description ? (
                          <CustomTooltip
                            show
                            size="small"
                            title={x.description}
                            placement="left"
                          >
                            <span>{x.label}</span>
                          </CustomTooltip>
                        ) : (
                          x.label
                        )
                      }
                      componentsProps={{ typography: { typography: "s2" } }}
                      sx={{ m: 0, gap: 0.5 }}
                    />
                  ))}
                </Stack>
              </>
            )}
            {savedViews && (
              <>
                <Divider />
                <Heading>Saved views</Heading>
                <Box sx={{ px: 0.75, pb: 0.5 }}>
                  {model.loadError && (
                    <Box role="alert" sx={{ px: 0.75, pb: 0.5 }}>
                      <Typography typography="s3" color="error.main">
                        Couldn’t load saved views.
                      </Typography>
                      <Button size="small" onClick={() => model.reload?.()}>
                        Retry saved views
                      </Button>
                    </Box>
                  )}
                  {!model.loadError && model.views.length === 0 && (
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ px: 0.75, pb: 0.5 }}
                    >
                      {model.isLoading
                        ? "Loading saved views…"
                        : "No saved views yet."}
                    </Typography>
                  )}
                  {model.views.map((v) => (
                    <Stack
                      key={v.id}
                      direction="row"
                      alignItems="center"
                      sx={{
                        borderRadius: 0.75,
                        bgcolor:
                          model.active?.id === v.id
                            ? "action.selected"
                            : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() => {
                          setOpen(false);
                          model.apply(v);
                        }}
                        sx={{
                          flex: 1,
                          justifyContent: "flex-start",
                          typography: "s2",
                          minWidth: 0,
                          overflow: "hidden",
                          whiteSpace: "nowrap",
                          textOverflow: "ellipsis",
                        }}
                      >
                        <SavedViewLabel view={v} />
                      </Button>
                      {canDeleteView(v) && (
                        <CustomTooltip
                          show
                          size="small"
                          title="Delete view"
                          disableInteractive
                        >
                          <IconButton
                            size="small"
                            aria-label={`Delete ${v.name}`}
                            onClick={() => model.del(v)}
                          >
                            <Iconify icon="mdi:trash-can-outline" width={16} />
                          </IconButton>
                        </CustomTooltip>
                      )}
                    </Stack>
                  ))}
                  <Stack direction="row" gap={0.5} sx={{ pt: 0.25 }}>
                    <Button
                      size="small"
                      color="inherit"
                      startIcon={
                        <Iconify icon="mdi:content-save-outline" width={16} />
                      }
                      onClick={() => {
                        setOpen(false);
                        setNaming(true);
                      }}
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      Save as…
                    </Button>
                    <Button
                      size="small"
                      color="inherit"
                      startIcon={<Iconify icon="mdi:link-variant" width={16} />}
                      onClick={() => model.copyLink(null)}
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      Copy link to this view
                    </Button>
                  </Stack>
                </Box>
              </>
            )}
            <Divider sx={{ mt: 0.5 }} />
            <Box sx={{ px: 0.75, pt: 0.5 }}>
              <Button
                size="small"
                color="inherit"
                disabled={!modified}
                startIcon={<Iconify icon="mdi:restore" width={16} />}
                onClick={() => {
                  setOpen(false);
                  resetView();
                }}
                sx={{ typography: "s2" }}
              >
                Reset view
              </Button>
            </Box>
          </Box>
        </Paper>
      ) : (
        <Popover
          open={open}
          anchorEl={btnRef.current}
          onClose={() => setOpen(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { mt: 0.5, width: 300 } } }}
        >
          <Box role="dialog" aria-label="View options" sx={{ pb: 0.5 }}>
            <ChoicesBlock
              choices={choices}
              actions={actions}
              onDone={() => setOpen(false)}
            />
            {density && (
              <>
                <Heading>Density</Heading>
                <Box sx={{ px: 1.5, pb: 1 }}>
                  <ToggleButtonGroup
                    exclusive
                    fullWidth
                    size="small"
                    value={density.value}
                    onChange={(_, v) => v && density.onChange(v)}
                    aria-label="Row density"
                  >
                    {DENSITIES.map((d) => (
                      <ToggleButton
                        key={d.value}
                        value={d.value}
                        aria-label={d.label}
                        title={d.label}
                        sx={{
                          typography: "s3",
                          py: 0.5,
                          px: 0.5,
                          textTransform: "none",
                        }}
                      >
                        {SHORT_DENSITY[d.value] ?? d.label}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                </Box>
              </>
            )}
            {columns && (
              <>
                {density && <Divider />}
                <Heading>Columns</Heading>
                <Stack
                  direction="row"
                  alignItems="center"
                  sx={{ px: 1.5, pb: 1 }}
                >
                  <Typography typography="s2" sx={{ flex: 1 }}>
                    {`${shownCount} of ${totalCount} columns shown`}
                  </Typography>
                  <Button
                    size="small"
                    onClick={() => {
                      setHiddenOnly(false);
                      setOpen(false);
                      setColumnsOpen(true);
                    }}
                    sx={{ typography: "s2", minWidth: 0 }}
                  >
                    Edit…
                  </Button>
                </Stack>
              </>
            )}
            {(shownSections.length > 0 || extras?.length > 0) && (
              <>
                <Divider />
                <Heading>Show on this page</Heading>
                <Stack sx={{ px: 1.5, pb: 0.75 }}>
                  {shownSections.map((s) => (
                    <FormControlLabel
                      key={`s-${s.id}`}
                      control={
                        <Checkbox
                          size="small"
                          checked={s.open}
                          onChange={() => sec.setOpen(s.id, !s.open)}
                          sx={{ p: 0.5 }}
                        />
                      }
                      label={s.title}
                      componentsProps={{ typography: { typography: "s2" } }}
                      sx={{ m: 0, gap: 0.5 }}
                    />
                  ))}
                  {(extras ?? []).map((x) => (
                    <FormControlLabel
                      key={`x-${x.id}`}
                      control={
                        <Checkbox
                          size="small"
                          checked={Boolean(x.checked)}
                          onChange={() => x.onChange?.(!x.checked)}
                          sx={{ p: 0.5 }}
                        />
                      }
                      label={
                        x.description ? (
                          <CustomTooltip
                            show
                            size="small"
                            title={x.description}
                            placement="left"
                          >
                            <span>{x.label}</span>
                          </CustomTooltip>
                        ) : (
                          x.label
                        )
                      }
                      componentsProps={{ typography: { typography: "s2" } }}
                      sx={{ m: 0, gap: 0.5 }}
                    />
                  ))}
                </Stack>
              </>
            )}
            {savedViews && (
              <>
                <Divider />
                <Heading>Saved views</Heading>
                <Box sx={{ px: 0.75, pb: 0.5 }}>
                  {model.loadError && (
                    <Box role="alert" sx={{ px: 0.75, pb: 0.5 }}>
                      <Typography typography="s3" color="error.main">
                        Couldn’t load saved views.
                      </Typography>
                      <Button size="small" onClick={() => model.reload?.()}>
                        Retry saved views
                      </Button>
                    </Box>
                  )}
                  {!model.loadError && model.views.length === 0 && (
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ px: 0.75, pb: 0.5 }}
                    >
                      {model.isLoading
                        ? "Loading saved views…"
                        : "No saved views yet."}
                    </Typography>
                  )}
                  {model.views.map((v) => (
                    <Stack
                      key={v.id}
                      direction="row"
                      alignItems="center"
                      sx={{
                        borderRadius: 0.75,
                        bgcolor:
                          model.active?.id === v.id
                            ? "action.selected"
                            : "transparent",
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() => {
                          setOpen(false);
                          model.apply(v);
                        }}
                        sx={{
                          flex: 1,
                          justifyContent: "flex-start",
                          typography: "s2",
                          minWidth: 0,
                          overflow: "hidden",
                          whiteSpace: "nowrap",
                          textOverflow: "ellipsis",
                        }}
                      >
                        <SavedViewLabel view={v} />
                      </Button>
                      {canDeleteView(v) && (
                        <CustomTooltip
                          show
                          size="small"
                          title="Delete view"
                          disableInteractive
                        >
                          <IconButton
                            size="small"
                            aria-label={`Delete ${v.name}`}
                            onClick={() => model.del(v)}
                          >
                            <Iconify icon="mdi:trash-can-outline" width={16} />
                          </IconButton>
                        </CustomTooltip>
                      )}
                    </Stack>
                  ))}
                  <Stack direction="row" gap={0.5} sx={{ pt: 0.25 }}>
                    <Button
                      size="small"
                      color="inherit"
                      startIcon={
                        <Iconify icon="mdi:content-save-outline" width={16} />
                      }
                      onClick={() => {
                        setOpen(false);
                        setNaming(true);
                      }}
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      Save as…
                    </Button>
                    <Button
                      size="small"
                      color="inherit"
                      startIcon={<Iconify icon="mdi:link-variant" width={16} />}
                      onClick={() => model.copyLink(null)}
                      sx={{ typography: "s2", color: "text.secondary" }}
                    >
                      Copy link to this view
                    </Button>
                  </Stack>
                </Box>
              </>
            )}
            <Divider sx={{ mt: 0.5 }} />
            <Box sx={{ px: 0.75, pt: 0.5 }}>
              <Button
                size="small"
                color="inherit"
                disabled={!modified}
                startIcon={<Iconify icon="mdi:restore" width={16} />}
                onClick={() => {
                  setOpen(false);
                  resetView();
                }}
                sx={{ typography: "s2" }}
              >
                Reset view
              </Button>
            </Box>
          </Box>
        </Popover>
      )}
      {columns && (
        <Popover
          open={columnsOpen}
          anchorEl={btnRef.current}
          onClose={() => setColumnsOpen(false)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { mt: 0.5 } } }}
        >
          <ColumnsPanel
            layout={columns}
            groups={groups}
            hiddenOnly={hiddenOnly}
            onHiddenOnlyChange={setHiddenOnly}
            onRequestClose={() => setColumnsOpen(false)}
          />
        </Popover>
      )}
      {savedViews && (
        <SaveViewDialog
          open={naming}
          onClose={() => setNaming(false)}
          onSave={model.save}
          isSaving={model.isSaving}
          canShare={savedViews.canShare}
        />
      )}
    </>
  );
}

function Dot({ sx }) {
  return (
    <Box
      component="span"
      aria-label="View changed"
      sx={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        bgcolor: "amber.500",
        display: "inline-block",
        ...sx,
      }}
    />
  );
}
Dot.propTypes = { sx: PropTypes.object };

ViewMenu.propTypes = {
  // IT-R4 G6 (S6, additive): radio groups and one-click actions on top.
  choices: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.node.isRequired,
      value: PropTypes.any,
      options: PropTypes.arrayOf(
        PropTypes.shape({ value: PropTypes.any, label: PropTypes.node }),
      ),
      onChange: PropTypes.func,
    }),
  ),
  actions: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.node.isRequired,
      icon: PropTypes.string,
      onClick: PropTypes.func,
    }),
  ),
  surface: PropTypes.string.isRequired,
  scopeId: PropTypes.string,
  density: PropTypes.shape({
    value: PropTypes.string,
    onChange: PropTypes.func,
  }),
  columns: PropTypes.object,
  groups: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
    }),
  ),
  sections: PropTypes.bool,
  savedViews: PropTypes.shape({
    state: PropTypes.object,
    onApply: PropTypes.func,
    canShare: PropTypes.bool,
  }),
  extras: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      checked: PropTypes.bool,
      onChange: PropTypes.func,
      description: PropTypes.string,
    }),
  ),
  isModified: PropTypes.bool,
  onReset: PropTypes.func,
  iconOnly: PropTypes.bool,
  inline: PropTypes.bool,
};

export { ViewMenu };
