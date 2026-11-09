import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Button, Link, Popover, Stack, Typography } from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { invalidateR2Reads } from "src/api/develop/r2";
import { resultsGridHref } from "src/sections/common/Analysis/analysisLinks";
import { runLabel } from "src/sections/common/Runs/runLabel";
import notify from "src/sections/common/ux/toast";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import useNotRecordedRuns from "./useNotRecordedRuns";
import {
  NOT_RECORDED_HELP,
  notRecordedCountText,
  notRecordedRowText,
  recordedText,
} from "./recordModel";

const Row = ({ run, datasetId, canEdit, onOpen }) => {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const label = runLabel(run);
  const record = async () => {
    setBusy(true);
    try {
      await axios.post(
        endpoints.develop.kh.recordRun(run.experiment_id, run.run_id),
      );
      invalidateR2Reads(queryClient, datasetId);
      notify(recordedText(label), {
        variant: "success",
        key: `record-${run.run_id}`,
      });
    } catch (err) {
      notify(
        `Couldn't record ${label}: ${getRequestErrorMessage(err, "try again")}`,
        { variant: "error", key: `record-${run.run_id}` },
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="not-recorded-row"
      data-run-id={run.run_id}
    >
      <Typography
        typography="s2"
        sx={{ flex: 1, minWidth: 0, fontVariantNumeric: "tabular-nums" }}
      >
        {notRecordedRowText(run, Date.now())}
      </Typography>
      <Button
        size="small"
        color="inherit"
        onClick={() => onOpen(run)}
        data-testid="not-recorded-open"
        sx={{ typography: "s2", flexShrink: 0 }}
      >
        Open
      </Button>
      <CustomTooltip
        show={!canEdit}
        title="Editors can record runs"
        size="small"
        arrow
      >
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled={!canEdit || busy}
            onClick={record}
            data-testid="not-recorded-record"
            sx={{ typography: "s2", flexShrink: 0 }}
          >
            Record it
          </Button>
        </span>
      </CustomTooltip>
    </Stack>
  );
};

Row.propTypes = {
  run: PropTypes.object.isRequired,
  datasetId: PropTypes.string.isRequired,
  canEdit: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

/**
 * K-2 (RT-09): the leaderboard footer's `1 not recorded · Show`. The
 * popover lists each run started with Record off, when it is deleted, and
 * `Open` (its results, by link) / `Record it` (it joins the leaderboard).
 * Absent when there is none. Quick tests keep their own link.
 */
export default function NotRecordedLink({ datasetId }) {
  const navigate = useNavigate();
  const { role } = useAuthContext();
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const { data: runs } = useNotRecordedRuns(datasetId);
  const [anchor, setAnchor] = useState(null);
  const n = runs?.length ?? 0;
  useEffect(() => {
    if (!n) setAnchor(null);
  }, [n]);
  if (!n) return null;
  const open = (run) => {
    setAnchor(null);
    navigate(
      resultsGridHref({ mode: "compare", datasetId, runIds: [run.run_id] }),
    );
  };
  return (
    <>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        data-testid="not-recorded-link"
      >
        {`${notRecordedCountText(n)} · `}
        <Link
          component="button"
          type="button"
          typography="s3"
          underline="hover"
          aria-haspopup="dialog"
          aria-expanded={Boolean(anchor)}
          data-testid="not-recorded-toggle"
          onClick={(e) => setAnchor(e.currentTarget)}
        >
          Show
        </Link>
      </Typography>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: { p: 1.5, width: 460, maxWidth: "calc(100vw - 32px)" },
            "data-testid": "not-recorded-popover",
            role: "dialog",
            "aria-label": "Runs not recorded",
          },
        }}
      >
        <Stack spacing={1}>
          <Typography typography="s2" fontWeight={600}>
            Not recorded
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {NOT_RECORDED_HELP}
          </Typography>
          {runs.map((run) => (
            <Row
              key={run.run_id}
              run={run}
              datasetId={datasetId}
              canEdit={canEdit}
              onOpen={open}
            />
          ))}
        </Stack>
      </Popover>
    </>
  );
}

NotRecordedLink.propTypes = { datasetId: PropTypes.string };
