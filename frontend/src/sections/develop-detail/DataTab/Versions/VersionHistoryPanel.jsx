import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Divider,
  IconButton,
  InputAdornment,
  Skeleton,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useAuthContext } from "src/auth/hooks";
import { ROLES } from "src/utils/rolePermissionMapping";
import { copyText } from "src/sections/common/ux/CopyButton";
import EmptyState from "src/sections/common/ux/EmptyState";
import { notify } from "src/sections/common/ux/toast";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useQueryClient } from "@tanstack/react-query";
import {
  datasetVersionsKey,
  useAsOfVersion,
  useUpdateDatasetVersion,
} from "src/api/develop/dataset-versions";
import { useRunExperimentStore } from "../../states";
import { usePendingChangesGate } from "./PendingChangesGate";
import TagVersionDialog from "./TagVersionDialog";
import useVersionUrl from "./useVersionUrl";
import VersionChangesDialog from "./VersionChangesDialog";
import VersionHistoryEntry from "./VersionHistoryEntry";
import { useRestoreDialog } from "./RestoreVersionDialog";
import { filterVersions, groupVersions } from "./versionText";

export const HISTORY_PANEL_WIDTH = 380;
// Below this width the panel overlays the grid instead of squeezing it.
const OVERLAY_BELOW = 1600;
// `Filter versions…` shows from this many versions (A1-34, data-driven).
export const FILTER_FROM = 6;
const FILTER_CHIPS = [
  { key: "tagged", label: "Tagged" },
  { key: "mine", label: "Mine" },
  { key: "used", label: "Used by experiments" },
];

const History = ({ datasetId, onClose }) => {
  const {
    versions,
    latest,
    version: viewing,
    isAsOf,
    isLoading,
  } = useAsOfVersion(datasetId);
  const { viewVersion, update: updateUrl } = useVersionUrl();
  // W3-9: opening the history reads it fresh, so a re-test started since
  // the versions were last read shows on its version.
  const queryClient = useQueryClient();
  useEffect(() => {
    if (datasetId)
      queryClient.invalidateQueries({
        queryKey: datasetVersionsKey(datasetId),
      });
  }, [datasetId, queryClient]);
  const gate = usePendingChangesGate(datasetId);
  const openRestore = useRestoreDialog((st) => st.open);
  const { user, role } = useAuthContext();
  const viewer = role === ROLES.VIEWER || role === ROLES.WORKSPACE_VIEWER;
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState([]);
  const [tagging, setTagging] = useState(null);
  const [changes, setChanges] = useState(null);
  // F5: the history filter lives in the URL (`hq`, `hchips`).
  const [hv, setHv] = useViewUrlState({ hq: "", hchips: [] });
  const now = Date.now();
  const shown = useMemo(
    () =>
      filterVersions(versions, {
        q: hv.hq,
        chips: hv.hchips,
        me: user ? { id: user.id, name: user.name } : null,
      }),
    [versions, hv.hq, hv.hchips, user],
  );
  const groups = useMemo(() => groupVersions(shown, now), [shown, now]);
  const viewingNumber = isAsOf ? viewing?.number : latest?.number;
  const filtered = Boolean(hv.hq || hv.hchips.length);
  const pickLabel = (id) => {
    const i = picked.indexOf(id);
    if (i < 0) return null;
    if (picked.length < 2) return "A";
    const [a, b] = picked
      .map((x) => versions.find((v) => v.id === x))
      .sort((x, y) => x.number - y.number);
    return a?.id === id ? "A" : b?.id === id ? "B" : null;
  };
  const pickedSorted = picked
    .map((id) => versions.find((v) => v.id === id))
    .filter(Boolean)
    .sort((x, y) => x.number - y.number);
  // A1-10: the version chip's `Compare two versions…` opens the panel with
  // `hcmp=1`; start compare mode once the versions are in, then drop it.
  useEffect(() => {
    if (isLoading || versions.length < 2) return;
    if (new URLSearchParams(window.location.search).get("hcmp") !== "1") return;
    setSelecting(true);
    updateUrl((p) => p.delete("hcmp"));
  }, [isLoading, versions.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // RV#38: entering compare mode pre-checks the version you are viewing.
  useEffect(() => {
    if (!selecting) return;
    const cur = versions.find((v) => v.number === viewingNumber);
    if (cur) setPicked([cur.id]);
  }, [selecting]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (v) =>
    setPicked((cur) =>
      cur.includes(v.id)
        ? cur.filter((id) => id !== v.id)
        : cur.length < 2
          ? [...cur, v.id]
          : cur,
    );

  const updateVersion = useUpdateDatasetVersion(datasetId);
  const onAction = (key, v, extra) => {
    // W3-9: `Add to this version's note` appends the re-test result (N5).
    if (key === "retest-note" && extra)
      updateVersion.mutate(
        {
          versionId: v.id,
          description: [v.description, extra].filter(Boolean).join(" · "),
        },
        {
          onSuccess: () =>
            notify(`Added the re-test result to v${v.number}'s note`, {
              key: "retest-note",
              variant: "success",
            }),
          onError: () =>
            notify("Couldn't update the note", {
              key: "retest-note",
              variant: "error",
            }),
        },
      );
    if (key === "view") viewVersion(v.number, { latestNumber: latest?.number });
    if (key === "changes")
      setChanges({
        versionId: v.id,
        against: "parent",
        title: `Changes in v${v.number}`,
        subtitle: v.description,
      });
    if (key === "tag") setTagging(v);
    if (key === "experiment")
      gate("experiment", () =>
        // L5 reads `prefill.version_id`; the stub opens the drawer.
        useRunExperimentStore
          .getState()
          .initiateCreateMode({ prefill: { version_id: v.id } }),
      );
    if (key === "restore" && !viewer) openRestore(v);
    if (key === "copy")
      copyText(v.id).then((ok) =>
        notify(
          ok
            ? `Copied the id of v${v.number}`
            : "Couldn't copy to the clipboard",
          { key: "version-id-copy", variant: ok ? "success" : "error" },
        ),
      );
  };

  const compare = () => {
    const [a, b] = picked
      .map((id) => versions.find((v) => v.id === id))
      .sort((x, y) => x.number - y.number);
    setChanges({
      versionId: a.id,
      against: b.id,
      title: `Compare v${a.number} and v${b.number}`,
      subtitle: null,
    });
  };

  // W3-9: `Compare production with staging` once both environments are set.
  const prodV = versions.find((v) => (v.tags ?? []).includes("production"));
  const stagingV = versions.find((v) => (v.tags ?? []).includes("staging"));
  const compareEnvs = () =>
    setChanges({
      versionId: prodV.id,
      against: stagingV.id,
      title: "Compare production with staging",
      subtitle: `v${prodV.number} (production) and v${stagingV.number} (staging)`,
    });

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box sx={{ px: 2, pt: 1.5, pb: 1.25, flexShrink: 0 }}>
        <Stack direction="row" alignItems="center">
          <Typography typography="m3" fontWeight="fontWeightSemiBold">
            Version history
          </Typography>
          <Box sx={{ flex: 1 }} />
          <CustomTooltip
            show
            title="Close"
            placement="bottom"
            arrow
            size="small"
          >
            <IconButton
              size="small"
              aria-label="Close version history"
              onClick={onClose}
            >
              <Iconify icon="mingcute:close-line" width={18} />
            </IconButton>
          </CustomTooltip>
        </Stack>
        <Typography typography="s2" color="text.secondary">
          {isLoading
            ? " "
            : `${versions.length} version${versions.length === 1 ? "" : "s"} · v${latest?.number} is latest`}
        </Typography>
        {/* A1-33: Compare always (disabled below two versions). */}
        <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 1.25 }}>
          <CustomTooltip
            show
            size="small"
            placement="bottom"
            arrow
            disableInteractive
            title={
              versions.length < 2
                ? "Compare needs at least two versions"
                : selecting
                  ? "Leave compare mode"
                  : "Pick two versions to see every row that changed between them"
            }
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                color="primary"
                disabled={versions.length < 2}
                onClick={() => {
                  setSelecting((st) => !st);
                  setPicked([]);
                }}
                sx={{ height: 30 }}
                data-testid="history-compare-mode"
              >
                {selecting ? "Cancel" : "Compare two versions"}
              </Button>
            </span>
          </CustomTooltip>
          {selecting && (
            <Typography typography="s2" color="text.secondary">
              {picked.length === 2
                ? `A = v${pickedSorted[0]?.number} · B = v${pickedSorted[1]?.number}`
                : picked.length === 1
                  ? `A = v${pickedSorted[0]?.number} · pick B`
                  : "Pick two versions"}
            </Typography>
          )}
        </Stack>
        {prodV && stagingV && prodV.id !== stagingV.id && (
          <Chip
            size="small"
            variant="outlined"
            icon={<Iconify icon="mdi:compare-horizontal" width={14} />}
            label="Compare production with staging"
            onClick={compareEnvs}
            data-testid="compare-environments"
            sx={{ mt: 1, typography: "s3", height: 24 }}
          />
        )}
        {/* A1-34: the filter earns its room from 6 versions (or while a
            filter is on, so it can be cleared). */}
        {(versions.length >= FILTER_FROM || filtered) && (
          <Stack gap={0.75} sx={{ mt: 1.25 }}>
            <TextField
              size="small"
              value={hv.hq}
              onChange={(e) => setHv({ hq: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Escape" && hv.hq) {
                  e.stopPropagation();
                  setHv({ hq: "" });
                }
              }}
              placeholder="Filter versions…"
              inputProps={{
                "aria-label": "Filter versions",
                "data-testid": "history-filter",
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon="eva:search-fill" width={16} />
                  </InputAdornment>
                ),
              }}
              sx={{ "& .MuiInputBase-input": { typography: "s2", py: 0.625 } }}
            />
            <Stack direction="row" gap={0.75} sx={{ flexWrap: "wrap" }}>
              {FILTER_CHIPS.map((c) => {
                const on = hv.hchips.includes(c.key);
                return (
                  <Chip
                    key={c.key}
                    size="small"
                    label={c.label}
                    variant={on ? "filled" : "outlined"}
                    color={on ? "primary" : "default"}
                    onClick={() =>
                      setHv({
                        hchips: on
                          ? hv.hchips.filter((k) => k !== c.key)
                          : [...hv.hchips, c.key],
                      })
                    }
                    sx={{ typography: "s3", height: 24 }}
                  />
                );
              })}
            </Stack>
          </Stack>
        )}
      </Box>
      <Divider />
      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 2, py: 1.5 }}>
        {isLoading &&
          [0, 1, 2, 3].map((i) => (
            <Skeleton key={i} variant="rounded" height={104} sx={{ mb: 1.5 }} />
          ))}
        {!isLoading && filtered && shown.length === 0 && (
          <EmptyState
            compact
            title="No versions match"
            body="Try another word, or clear the filters."
            onClearFilters={() => setHv({ hq: "", hchips: [] })}
          />
        )}
        {groups.map((g) => (
          <Box key={g.label} sx={{ mb: 1.5 }}>
            <Typography
              typography="s3"
              color="text.secondary"
              fontWeight="fontWeightMedium"
              sx={{ textTransform: "uppercase", letterSpacing: 0.4, mb: 0.75 }}
            >
              {g.label}
            </Typography>
            <Stack gap={1}>
              {g.versions.map((v) => (
                <VersionHistoryEntry
                  key={v.id}
                  version={v}
                  now={now}
                  viewing={v.number === viewingNumber}
                  selectable={selecting}
                  checked={picked.includes(v.id)}
                  checkDisabled={picked.length === 2 && !picked.includes(v.id)}
                  onToggleCheck={() => toggle(v)}
                  pickLabel={selecting ? pickLabel(v.id) : null}
                  nextNumber={latest ? latest.number + 1 : null}
                  canRestore={!viewer}
                  canWrite={!viewer}
                  onOpen={() =>
                    viewVersion(v.number, { latestNumber: latest?.number })
                  }
                  onAction={onAction}
                />
              ))}
            </Stack>
          </Box>
        ))}
      </Box>
      {selecting && (
        <Box
          sx={{
            p: 2,
            pt: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Button
            fullWidth
            size="small"
            variant="contained"
            color="primary"
            disabled={picked.length !== 2}
            onClick={compare}
            data-testid="history-compare"
          >
            {picked.length === 2
              ? `Compare v${pickedSorted[0]?.number} → v${pickedSorted[1]?.number}`
              : "Pick two versions to compare"}
          </Button>
        </Box>
      )}
      <TagVersionDialog
        open={Boolean(tagging)}
        datasetId={datasetId}
        version={tagging}
        onClose={() => setTagging(null)}
      />
      <VersionChangesDialog
        open={Boolean(changes)}
        datasetId={datasetId}
        versionId={changes?.versionId}
        against={changes?.against}
        title={changes?.title}
        subtitle={changes?.subtitle}
        onClose={() => setChanges(null)}
      />
    </Stack>
  );
};

History.propTypes = {
  datasetId: PropTypes.string,
  onClose: PropTypes.func,
};

/**
 * The dataset Version history (PRODUCT-SPEC §3.5), docked in DataTabLayout's
 * right slot while `?history=1` (`open`), 380 px. It renders `grid` (the
 * RowViewPanel holding the Data grid) at the same element position whether
 * open or closed, so AG Grid never remounts; the panel is a sibling after it.
 */
const VersionHistoryPanel = ({ open, grid }) => {
  const { datasetId, setHistoryOpen } = useVersionUrl();
  // DG#37: below 1600 px the panel overlays the grid (no squeezed columns).
  const overlay = useMediaQuery(`(max-width:${OVERLAY_BELOW - 0.02}px)`);
  // Focus moves into the panel when it opens, so Esc closes it at once.
  const asideRef = useRef(null);
  useEffect(() => {
    if (open) asideRef.current?.focus({ preventScroll: true });
  }, [open]);
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        position: "relative",
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex" }}>
        {grid}
      </Box>
      {open && (
        <Box
          component="aside"
          ref={asideRef}
          tabIndex={-1}
          aria-label="Version history"
          data-testid="version-history-panel"
          // The Falcon bubble hides while the panel is open (G0 policy).
          data-fa-hide-fab
          // DG#37: Esc closes (from anywhere inside the panel).
          onKeyDown={(e) => {
            if (e.key === "Escape" && !e.defaultPrevented) {
              e.stopPropagation();
              setHistoryOpen(false);
            }
          }}
          sx={{
            width: HISTORY_PANEL_WIDTH,
            flexShrink: 0,
            minHeight: 0,
            borderLeft: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
            outline: "none",
            ...(overlay && {
              position: "absolute",
              top: 0,
              right: 0,
              bottom: 0,
              zIndex: 5,
              boxShadow: (t) => t.customShadows?.z16 ?? t.shadows[8],
            }),
          }}
        >
          <History
            datasetId={datasetId}
            onClose={() => setHistoryOpen(false)}
          />
        </Box>
      )}
    </Box>
  );
};

VersionHistoryPanel.propTypes = {
  open: PropTypes.bool,
  grid: PropTypes.node,
};

export default VersionHistoryPanel;
