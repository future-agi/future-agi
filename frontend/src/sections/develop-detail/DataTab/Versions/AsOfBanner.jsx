import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Divider,
  GlobalStyles,
  IconButton,
  Popover,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import {
  useAsOfVersion,
  useVersionChanges,
} from "src/api/develop/dataset-versions";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import ExperimentLinks from "./ExperimentLinks";
import { useRestoreDialog } from "./RestoreVersionDialog";
import useVersionUrl from "./useVersionUrl";
import {
  VERSION_CELL_CLASS,
  VERSION_ROW_DELETED_CLASS,
  useAsOfView,
  useAsOfViewStore,
} from "./versionedSource";

/** Grid marks for as-of views (classes come from versionedSource rules). */
const GridMarks = () => (
  <GlobalStyles
    styles={(t) => ({
      [`.dataset-table .${VERSION_CELL_CLASS}`]: {
        outline: `1.5px dashed ${t.palette.info.main}`,
        outlineOffset: -3,
        backgroundColor: alpha(t.palette.info.main, 0.06),
      },
      [`.dataset-table .ag-row.${VERSION_ROW_DELETED_CLASS} .ag-cell`]: {
        textDecoration: "line-through",
        opacity: 0.55,
      },
      // The change tooltips ("Latest (v6): …") in the CustomTooltip look.
      ".dataset-table .ag-tooltip": {
        maxWidth: 420,
        padding: "6px 10px",
        borderRadius: 4,
        border: `1px solid ${t.palette.divider}`,
        backgroundColor: t.palette.background.paper,
        color: t.palette.text.primary,
        boxShadow: t.customShadows?.z8 ?? t.shadows[4],
        fontSize: 12,
        lineHeight: "18px",
        whiteSpace: "normal",
      },
    })}
  />
);

const ChangeChip = ({ count, label, active, dashed, tooltip, onClick }) => (
  <CustomTooltip show title={tooltip} placement="bottom" arrow size="small">
    <span>
      <Box
        component="button"
        type="button"
        disabled={!count}
        aria-pressed={active}
        onClick={onClick}
        sx={(t) => ({
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          height: 24,
          px: 1,
          borderRadius: 0.5,
          border: `1px ${dashed ? "dashed" : "solid"}`,
          borderColor: active ? "info.main" : dashed ? "info.main" : "divider",
          bgcolor: active
            ? alpha(t.palette.info.main, 0.14)
            : "background.paper",
          color: count ? "text.primary" : "text.disabled",
          typography: "s2",
          fontWeight: 500,
          fontFamily: "inherit",
          cursor: count ? "pointer" : "default",
          fontVariantNumeric: "tabular-nums",
          "&:hover:not(:disabled)": { borderColor: "info.main" },
        })}
      >
        {count} {label}
        {active && <Iconify icon="mingcute:close-line" width={14} />}
      </Box>
    </span>
  </CustomTooltip>
);

ChangeChip.propTypes = {
  count: PropTypes.number,
  label: PropTypes.string,
  active: PropTypes.bool,
  dashed: PropTypes.bool,
  tooltip: PropTypes.string,
  onClick: PropTypes.func,
};

const frameSx = {
  mx: 1.5,
  mt: 1,
  px: 1.5,
  py: 1,
  borderRadius: 1,
  border: "1px solid",
  borderColor: (t) => alpha(t.palette.info.main, 0.3),
  bgcolor: (t) => alpha(t.palette.info.main, 0.06),
};

/**
 * AsOfBanner (PRODUCT-SPEC §3.5), DataTabLayout `top`.
 * As of a version (`?version=5`): one 40 px line (R3, RV#33) — see AsOfLine.
 * Latest filtered to rows added after a version: `Rows added after v5 ×`.
 * It also keeps the grid in step: a version or chip change purges the
 * server-side cache, and a stale `?version=` (unknown, or latest) is dropped.
 */
const AsOfBanner = () => {
  const { datasetId, asOfNumber, viewVersion, update } = useVersionUrl();
  const { version, latest, isAsOf, versions, isLoading } =
    useAsOfVersion(datasetId);
  const view = useAsOfView(datasetId);
  const setView = useAsOfViewStore((s) => s.setView);
  const { gridApi } = useDevelopDetailContext();

  const since = useVersionChanges(datasetId, version?.id, {
    against: "latest",
    enabled: isAsOf,
  });
  const inThis = useVersionChanges(datasetId, version?.id, {
    against: "parent",
    enabled: isAsOf,
  });

  // Drop `?version=` that names latest or no version at all.
  useEffect(() => {
    if (asOfNumber == null || isLoading || !versions.length) return;
    const known = versions.some((v) => v.number === asOfNumber);
    if (!known || asOfNumber === latest?.number)
      update((p) => p.delete("version"));
  }, [asOfNumber, isLoading, versions, latest?.number, update]);

  // Re-read the grid when the view changes (not on first mount).
  const viewKey = `${asOfNumber}|${view.compare}|${view.change}|${view.addedSince}`;
  const lastKey = useRef(viewKey);
  useEffect(() => {
    if (lastKey.current === viewKey) return;
    lastKey.current = viewKey;
    gridApi?.current?.refreshServerSide?.({ purge: true });
  }, [viewKey, gridApi]);

  if (!isAsOf && view.addedSince != null) {
    const n = view.addedSince;
    return (
      <>
        <GridMarks />
        <Stack direction="row" alignItems="center" gap={1} sx={frameSx}>
          <Iconify
            icon="mdi:filter-variant"
            width={18}
            sx={{ color: "info.main" }}
          />
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              height: 24,
              pl: 1,
              pr: 0.25,
              borderRadius: 0.5,
              bgcolor: (t) => alpha(t.palette.info.main, 0.14),
              typography: "s2",
              fontWeight: 500,
            }}
          >
            Rows added after v{n}
            <IconButton
              size="small"
              aria-label="Clear the filter"
              onClick={() => setView(datasetId, { addedSince: null })}
              sx={{ p: 0.25 }}
            >
              <Iconify icon="mingcute:close-line" width={14} />
            </IconButton>
          </Box>
          <Typography typography="s2" color="text.secondary">
            Latest v{latest?.number} · generated columns included
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => viewVersion(n, { latestNumber: latest?.number })}
            sx={{ bgcolor: "background.paper" }}
          >
            Back to v{n}
          </Button>
        </Stack>
      </>
    );
  }

  if (!isAsOf || !version) return <GridMarks />;

  return (
    <>
      <GridMarks />
      <AsOfLine
        datasetId={datasetId}
        version={version}
        latest={latest}
        view={view}
        setView={setView}
        since={since.data?.counts ?? null}
        inThis={inThis.data?.counts ?? null}
        onBack={() => viewVersion(null)}
        onAddedSince={() => {
          viewVersion(null);
          setView(datasetId, { addedSince: version.number });
        }}
      />
    </>
  );
};

/** `2 edited · 10 added` — the non-zero change parts. */
const changeWords = (c) =>
  !c
    ? ""
    : [
        c.modified ? `${c.modified} edited` : null,
        c.deleted ? `${c.deleted} deleted` : null,
        c.added ? `${c.added} added` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "no row changes";

const CHANGE_LABEL = { modified: "edited", deleted: "deleted", added: "added" };

/**
 * RV#33/34: ONE 40 px line for an as-of view —
 *   ⟲ Viewing v5 · golden-v3 · 6d ago by Asha Rao  🔒 Read-only
 *        [Changes since v5 · 2 edited · 10 added ▾] [Restore as v7…] [Back to latest (v6)]
 * The change chips (filters, `Since` / `In this version`) live in the
 * popover; an active filter shows on the button with a clear ×.
 */
const AsOfLine = ({
  datasetId,
  version,
  latest,
  view,
  setView,
  since,
  inThis,
  onBack,
  onAddedSince,
}) => {
  const [anchor, setAnchor] = useState(null);
  const openRestore = useRestoreDialog((st) => st.open);
  const { role } = useAuthContext();
  const viewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const counts = view.compare === "in" ? inThis : since;
  const prev = version.number - 1;
  const next = (latest?.number ?? version.number) + 1;
  const toggleChange = (change) =>
    setView(datasetId, { change: view.change === change ? null : change });
  const active = view.change;

  useRegisterCommands(
    "as-of-version",
    [
      {
        id: "version.restore",
        label: `Restore v${version.number} as v${next}…`,
        keywords: ["restore this version", "roll back"],
        icon: "mdi:restore",
        run: () => openRestore(version),
        when: () => !viewer,
      },
      {
        id: "version.changes",
        label: `Show changes since v${version.number}`,
        keywords: ["changes since this version", "diff"],
        icon: "mdi:file-compare",
        run: () => setAnchor(document.querySelector("[data-asof-changes]")),
      },
      {
        id: "version.latest",
        label: `Back to latest (v${latest?.number})`,
        icon: "mdi:history",
        run: onBack,
      },
    ],
    [version.number, next, viewer, latest?.number],
  );

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      role="region"
      aria-label={`Viewing v${version.number}`}
      data-testid="as-of-banner"
      sx={{ ...frameSx, py: 0, height: 40, minWidth: 0 }}
    >
      <Iconify
        icon="mdi:history"
        width={18}
        sx={{ color: "info.main", flexShrink: 0 }}
      />
      <CustomTooltip
        show={Boolean(version.description)}
        size="small"
        placement="bottom"
        arrow
        title={`“${version.description ?? ""}”`}
      >
        <Typography
          typography="s1"
          color="text.primary"
          noWrap
          sx={{ minWidth: 0, flexShrink: 1 }}
        >
          Viewing{" "}
          <Box component="span" sx={{ fontWeight: 600 }}>
            v{version.number}
          </Box>
          {version.tags?.[0] ? ` · ${version.tags[0]}` : ""} ·{" "}
          <RelativeTime iso={version.created_at} /> by{" "}
          {version.created_by?.name ?? "Unknown"}
        </Typography>
      </CustomTooltip>
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        title="Past versions are read-only. Restore this one to make it the latest, or go back to latest to edit."
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{
            flexShrink: 0,
            px: 0.75,
            height: 22,
            borderRadius: 0.5,
            bgcolor: "action.hover",
            color: "text.secondary",
            typography: "s3",
            fontWeight: 600,
          }}
        >
          <Iconify icon="mdi:lock-outline" width={13} />
          Read-only
        </Stack>
      </CustomTooltip>
      <Box sx={{ flex: 1, minWidth: 8 }} />
      <Stack direction="row" alignItems="center" sx={{ flexShrink: 0 }}>
        <Button
          size="small"
          color="inherit"
          data-asof-changes
          data-testid="as-of-changes"
          onClick={(e) => setAnchor(e.currentTarget)}
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{
            typography: "s2",
            color: active ? "info.dark" : "text.secondary",
            bgcolor: active
              ? (t) => alpha(t.palette.info.main, 0.14)
              : "transparent",
            whiteSpace: "nowrap",
          }}
        >
          {active
            ? `Showing ${counts?.[active] ?? ""} ${CHANGE_LABEL[active]}`
            : `${view.compare === "in" ? "In" : "Changes since"} v${
                version.number
              } · ${changeWords(counts)}`}
        </Button>
        {active && (
          <CustomTooltip show size="small" arrow title="Show every row">
            <IconButton
              size="small"
              aria-label="Clear the change filter"
              onClick={() => setView(datasetId, { change: null })}
            >
              <Iconify icon="mingcute:close-line" width={14} />
            </IconButton>
          </CustomTooltip>
        )}
      </Stack>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { p: 1.5, width: 380 } } }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          fullWidth
          value={view.compare}
          onChange={(_, v) =>
            v && setView(datasetId, { compare: v, change: null })
          }
          sx={{
            "& .MuiToggleButton-root": {
              typography: "s2",
              py: 0.25,
              textTransform: "none",
              height: 28,
            },
          }}
        >
          <ToggleButton value="since">
            <CustomTooltip
              show
              size="small"
              arrow
              title={`Rows changed after v${version.number} (they are different in v${latest?.number})`}
            >
              <span>Since this version</span>
            </CustomTooltip>
          </ToggleButton>
          <ToggleButton value="in">
            <CustomTooltip
              show
              size="small"
              arrow
              title={`Rows v${version.number} itself changed (against v${prev})`}
            >
              <span>In this version</span>
            </CustomTooltip>
          </ToggleButton>
        </ToggleButtonGroup>
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ mt: 1.25, mb: 0.75 }}
        >
          Click a count to show only those rows. Click it again to show every
          row.
        </Typography>
        {counts ? (
          <Stack direction="row" gap={1} sx={{ flexWrap: "wrap" }}>
            {view.compare === "since" ? (
              <>
                <ChangeChip
                  count={counts.modified}
                  label="edited"
                  active={active === "modified"}
                  tooltip={
                    counts.modified
                      ? `Rows whose values changed after v${version.number}. Changed cells are outlined; hover one for the latest value.`
                      : `No row was edited after v${version.number}`
                  }
                  onClick={() => toggleChange("modified")}
                />
                <ChangeChip
                  count={counts.deleted}
                  label="deleted"
                  active={active === "deleted"}
                  tooltip={
                    counts.deleted
                      ? `Rows in v${version.number} that were deleted later (struck through)`
                      : `No row of v${version.number} was deleted later`
                  }
                  onClick={() => toggleChange("deleted")}
                />
                <ChangeChip
                  count={counts.added}
                  label="added"
                  tooltip={
                    counts.added
                      ? `Rows added after v${version.number}. Opens the latest version, filtered to them.`
                      : `No row was added after v${version.number}`
                  }
                  onClick={() => {
                    setAnchor(null);
                    onAddedSince();
                  }}
                />
              </>
            ) : (
              <>
                <ChangeChip
                  count={counts.modified}
                  label="edited"
                  active={active === "modified"}
                  tooltip={`Rows v${version.number} edited. Changed cells are outlined; hover one for the value in v${prev}.`}
                  onClick={() => toggleChange("modified")}
                />
                <ChangeChip
                  count={counts.deleted}
                  label="deleted"
                  active={active === "deleted"}
                  tooltip={`Rows v${version.number} deleted, shown as they were in v${prev}`}
                  onClick={() => toggleChange("deleted")}
                />
                <ChangeChip
                  count={counts.added}
                  label="added"
                  active={active === "added"}
                  tooltip={`Rows v${version.number} added`}
                  onClick={() => toggleChange("added")}
                />
              </>
            )}
          </Stack>
        ) : (
          <Typography typography="s2" color="text.secondary">
            Counting changes…
          </Typography>
        )}
        <Divider sx={{ my: 1.25 }} />
        <ExperimentLinks
          experiments={version.experiments}
          label={`Ran on v${version.number}`}
        />
        <Stack direction="row" alignItems="flex-start" gap={0.5} sx={{ mt: 1 }}>
          <Iconify
            icon="mdi:information-outline"
            width={14}
            sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
          />
          <Typography typography="s3" color="text.secondary">
            Generated columns show the latest values only. Open the latest
            version to see them.
          </Typography>
        </Stack>
      </Popover>
      {/* A1-36: always; viewers see it disabled with the reason. */}
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        arrow
        disableInteractive
        title={
          viewer
            ? "You have view access. Ask an editor to restore a version."
            : `Save a new version (v${next}) with the rows of v${version.number}`
        }
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            disabled={viewer}
            onClick={() => openRestore(version)}
            startIcon={<Iconify icon="mdi:restore" width={16} />}
            sx={{
              flexShrink: 0,
              whiteSpace: "nowrap",
              bgcolor: "background.paper",
            }}
            data-testid="as-of-restore"
          >
            Restore as v{next}…
          </Button>
        </span>
      </CustomTooltip>
      <Button
        size="small"
        variant="outlined"
        color="primary"
        onClick={onBack}
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          bgcolor: "background.paper",
        }}
        data-testid="as-of-back"
      >
        Back to latest (v{latest?.number})
      </Button>
    </Stack>
  );
};

AsOfLine.propTypes = {
  datasetId: PropTypes.string,
  version: PropTypes.object.isRequired,
  latest: PropTypes.object,
  view: PropTypes.object.isRequired,
  setView: PropTypes.func.isRequired,
  since: PropTypes.object,
  inThis: PropTypes.object,
  onBack: PropTypes.func.isRequired,
  onAddedSince: PropTypes.func.isRequired,
};

export default AsOfBanner;
