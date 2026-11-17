import React, { useEffect, useRef, useState } from "react";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import { useParams } from "react-router-dom";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { notify } from "src/sections/common/ux/toast";
import { alpha } from "@mui/material/styles";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import { useDevelopDetailContext } from "../../Context/DevelopDetailContext";
import {
  createdColumnsText,
  dropCreatedColumnsByDefault,
  useChangeSet,
  useCreatedColumnsStore,
} from "./changeSetStore";
import { useOpenSaveVersion } from "./PendingChangesGate";
import { pendingCountsText, pendingTotal } from "./versionText";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";

/**
 * PendingChangesBar (PRODUCT-SPEC §3.5): sticky under the grid while the
 * change-set has ops.
 *   ● Unsaved changes — 2 added · 5 edited · 1 deleted · 3 split changes
 *                                     [Discard] [Review & save version]
 * Discard confirms first. The change-set is pinned to the latest version at
 * its first op (the Save dialog's base version).
 */
const NO_COLS = [];

const PendingChangesBarInner = () => {
  const { dataset: datasetId } = useParams();
  const cs = useChangeSet(datasetId);
  const { data: versions } = useDatasetVersions(datasetId, {
    enabled: Boolean(datasetId && cs.hasPending),
  });
  const latest = latestOf(versions);
  const openSave = useOpenSaveVersion(datasetId);
  const { refreshGrid } = useDevelopDetailContext();
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  // the interface contract (DI-03 / DV-01): columns created with these changes (written at
  // once, not versioned) are disclosed; Discard offers to delete them,
  // ticked only when they all came from paste (the interface contract review).
  const createdCols = useCreatedColumnsStore(
    (s) => s.cols[datasetId] ?? NO_COLS,
  );
  const [dropCols, setDropCols] = useState(false);
  const openDiscard = () => {
    setDropCols(dropCreatedColumnsByDefault(createdCols));
    setConfirmDiscard(true);
  };
  const discardAll = async () => {
    const drop = dropCols ? createdCols : [];
    cs.discard();
    setConfirmDiscard(false);
    const gone = [];
    for (const c of drop) {
      try {
        // eslint-disable-next-line no-await-in-loop
        await axios.delete(endpoints.develop.deleteColumn(datasetId, c.id));
        gone.push(c);
      } catch {
        /* reported below */
      }
    }
    useCreatedColumnsStore.getState().clear(datasetId);
    if (drop.length)
      notify(
        gone.length === drop.length
          ? `Discarded · deleted ${createdColumnsText(gone)}`
          : `Discarded · couldn't delete ${createdColumnsText(
              drop.filter((c) => !gone.includes(c)),
            )}`,
        {
          key: "discard-columns",
          variant: gone.length === drop.length ? "success" : "error",
        },
      );
    refreshGrid?.();
  };

  const { hasPending, baseVersionId, setBaseVersion } = cs;
  useEffect(() => {
    if (hasPending && !baseVersionId && latest?.id) setBaseVersion(latest.id);
  }, [hasPending, baseVersionId, latest?.id, setBaseVersion]);

  // Toasts (Added a blank row · Undo, Discarded …) sit just above this
  // bar instead of covering Discard / Review & save (same as VerdictBar).
  const barRef = useRef(null);
  useEffect(() => {
    if (!hasPending) return undefined;
    const body = document.body;
    const sync = () => {
      const r = barRef.current?.getBoundingClientRect();
      if (!r || window.innerHeight - r.bottom > 24) {
        body.style.removeProperty("--fa-toast-offset");
        return;
      }
      const gap = Math.max(0, Math.round(window.innerHeight - r.top) + 4);
      body.style.setProperty("--fa-toast-offset", `${gap}px`);
    };
    // The bar lands at the bottom once the page settles (the Row view
    // opens in the same click): measure again after layout and on resizes.
    sync();
    const raf = requestAnimationFrame(sync);
    const t = setTimeout(sync, 300);
    const ro =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(sync) : null;
    if (ro && barRef.current) {
      ro.observe(barRef.current);
      ro.observe(body);
    }
    window.addEventListener("resize", sync);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
      ro?.disconnect();
      window.removeEventListener("resize", sync);
      body.style.removeProperty("--fa-toast-offset");
    };
  }, [hasPending]);

  if (!hasPending) return null;
  const total = pendingTotal(cs.counts);
  const base = versions?.find((v) => v.id === baseVersionId) ?? latest;

  return (
    <>
      <Box
        ref={barRef}
        sx={{
          position: "sticky",
          bottom: "-52px",
          zIndex: 2,
          display: "flex",
          justifyContent: "center",
          px: 1.5,
          pb: 1,
          // IT-R1 G5 P7: the page keeps 56 px free under the content for the
          // floating launchers (bottom right); the centred bar sits in that
          // strip, so the grid keeps its full height (no gap at 1366×768).
          mt: 0.5,
          mb: "-52px",
        }}
      >
        <Box
          role="status"
          aria-label="Unsaved changes"
          sx={{
            display: "inline-flex",
            alignItems: "center",
            gap: 1,
            maxWidth: "100%",
            pl: 1.5,
            pr: 0.75,
            py: 0.75,
            borderRadius: 1,
            border: "1px solid",
            borderColor: (t) => alpha(t.palette.amber[500], 0.45),
            bgcolor: (t) =>
              t.palette.mode === "dark"
                ? alpha(t.palette.amber[500], 0.12)
                : t.palette.warning.lighter,
            boxShadow: (t) => t.customShadows?.z8 ?? t.shadows[2],
          }}
        >
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: "amber.500",
              flexShrink: 0,
            }}
          />
          <Typography typography="s1" fontWeight="fontWeightSemiBold" noWrap>
            Unsaved changes
          </Typography>
          <Typography
            typography="s1"
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0, mr: 1.5 }}
          >
            — {pendingCountsText(cs.counts)}
            {createdCols.length
              ? ` · ${createdColumnsText(createdCols)} added now (not versioned)`
              : ""}
            {base ? ` · on top of v${base.number}` : ""}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={openDiscard}
            sx={{
              flexShrink: 0,
              bgcolor: "background.paper",
              "&:hover": { bgcolor: "background.paper" },
            }}
          >
            Discard
          </Button>
          <KeyHintTooltip
            title="Review the changes and save a new version"
            action="saveVersion"
          >
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => openSave()}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              data-testid="pending-save"
            >
              Review & save version
            </Button>
          </KeyHintTooltip>
        </Box>
      </Box>
      <ConfirmDialog
        open={confirmDiscard}
        onClose={() => setConfirmDiscard(false)}
        title={`Discard ${total} unsaved change${total === 1 ? "" : "s"}?`}
        content={
          <Stack gap={1}>
            <Typography typography="s1" color="text.secondary">
              {pendingCountsText(cs.counts)}. The grid goes back to v
              {base?.number ?? latest?.number}. This can&apos;t be undone.
            </Typography>
            {createdCols.length ? (
              <FormControlLabel
                data-testid="discard-created-columns"
                control={
                  <Checkbox
                    size="small"
                    checked={dropCols}
                    onChange={(e) => setDropCols(e.target.checked)}
                  />
                }
                label={
                  <Typography typography="s2">
                    {`Also delete ${createdColumnsText(createdCols)}: ${
                      createdCols.length === 1 ? "it was" : "they were"
                    } added with these changes and ${
                      createdCols.length === 1 ? "is" : "are"
                    } not part of any version.`}
                  </Typography>
                }
              />
            ) : null}
          </Stack>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            sx={{ px: 3 }}
            onClick={discardAll}
          >
            Discard
          </Button>
        }
      />
    </>
  );
};

/**
 * L1 (the capability contract): not on the real backend, where it has nothing to hold: the grid writes at once there.
 */
const PendingChangesBar = () =>
  datasetCaps().pendingChanges ? <PendingChangesBarInner /> : null;

export default PendingChangesBar;
