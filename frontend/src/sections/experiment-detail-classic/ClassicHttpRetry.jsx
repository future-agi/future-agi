import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { rerunCells } from "src/api/develop/experiment-run-actions";
import {
  appendExperimentVariant,
  appendHttpRowsBody,
  previewHttpAppendRows,
} from "src/api/develop/experiment-append";
import { isExperimentPending } from "src/api/develop/experiment-status-refresh";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
import {
  HTTP_RESEND_COPY as COPY,
  httpAddRowsLines,
  httpCellResendLines,
  httpFailedRowsLines,
  httpRefusalMessage,
  isUnknownRequestOutcome,
  newHttpActionKey,
} from "src/sections/common/Runs/httpResendCopy";
import {
  HTTP_ROW_LIMIT,
  addRowsPreviewOf,
  cellOutcomeUnknown,
  httpSourceOfColumn,
  httpSourcesOf,
  ranOnSelectedRows,
  retryCellBody,
  retryFailedRowsBody,
} from "./httpRetryModel";

// SRC S1b: Retry cell, Retry failed rows and Add new rows for HTTP sources on
// the classic experiment Data page. Each is an explicit action behind a
// confirm. A re-send says first that the endpoint may already have processed
// the earlier request. Every action is sent once with its own Idempotency-Key
// (no automatic retry). When its outcome is unknown the dialog stays open:
// Check again repeats the same request and key, which the server answers
// with the first admission, so rows are sent at most once.

function ResendLines({ lines }) {
  return (
    <Stack spacing={1}>
      {lines.map((line) => (
        <span key={line}>{line}</span>
      ))}
    </Stack>
  );
}

ResendLines.propTypes = {
  lines: PropTypes.arrayOf(PropTypes.string).isRequired,
};

/**
 * One explicit keyed HTTP action. `send(body, key)` is called once per
 * confirm; `checkAgain()` repeats the unconfirmed request exactly.
 */
function useKeyedHttpAction(send, { started, failed, onSent }) {
  const [pending, setPending] = useState(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const mutation = useMutation({
    mutationFn: ({ body, key }) => send(body, key),
    retry: false,
    meta: { errorHandled: true },
  });
  const finish = () => {
    setPending(null);
    setUnconfirmed(false);
  };
  const dispatch = (next, onClose) => {
    setPending(next);
    mutation.mutate(next, {
      onSuccess: () => {
        finish();
        onClose();
        enqueueSnackbar(started, { variant: "success" });
        onSent?.();
      },
      onError: (error) => {
        if (isUnknownRequestOutcome(error)) {
          setUnconfirmed(true);
          return;
        }
        finish();
        onClose();
        enqueueSnackbar(httpRefusalMessage(error, failed), {
          variant: "error",
        });
      },
    });
  };
  return {
    busy: mutation.isPending,
    unconfirmed,
    start: (body, onClose) => {
      if (mutation.isPending) return;
      dispatch(pending ?? { body, key: newHttpActionKey() }, onClose);
    },
    checkAgain: (onClose) => {
      if (mutation.isPending || !pending) return;
      dispatch(pending, onClose);
    },
    // Closing an unconfirmed action drops its key: the user checks the
    // results before choosing a new, separate action.
    dismiss: () => {
      if (mutation.isPending) return false;
      if (unconfirmed) {
        enqueueSnackbar(COPY.unconfirmed, { variant: "error" });
        onSent?.();
      }
      finish();
      return true;
    },
  };
}

function HttpActionDialog({
  action,
  open,
  title,
  lines,
  items,
  confirmLabel,
  onConfirm,
  onClose,
}) {
  const close = () => {
    if (action.dismiss()) onClose();
  };
  return (
    <ConfirmDialog
      open={open}
      title={title}
      body={
        <ResendLines
          lines={action.unconfirmed ? [...lines, COPY.checkAgainHelp] : lines}
        />
      }
      items={items}
      confirmLabel={action.unconfirmed ? COPY.checkAgain : confirmLabel}
      cancelLabel={action.unconfirmed ? COPY.close : COPY.cancel}
      danger={false}
      busy={action.busy}
      onConfirm={() =>
        action.unconfirmed ? action.checkAgain(onClose) : onConfirm()
      }
      onClose={close}
    />
  );
}

HttpActionDialog.propTypes = {
  action: PropTypes.object.isRequired,
  open: PropTypes.bool.isRequired,
  title: PropTypes.string.isRequired,
  lines: PropTypes.arrayOf(PropTypes.string).isRequired,
  items: PropTypes.arrayOf(PropTypes.string),
  confirmLabel: PropTypes.string.isRequired,
  onConfirm: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

/**
 * Retry cell for the classic grid. `request({columnId, rowId})` opens the
 * confirm and returns true for an HTTP source's output cell; it returns false
 * for every other cell, which keeps its existing retry.
 * @param {{experimentId: string, sources: object[], columns: object[],
 *   readCell?: Function, onSent?: Function}} options
 */
export function useClassicHttpCellRetry({
  experimentId,
  sources = [],
  columns = [],
  readCell,
  onSent,
}) {
  const [target, setTarget] = useState(null);
  const action = useKeyedHttpAction(
    (body, key) => rerunCells(experimentId, body, { idempotencyKey: key }),
    { started: COPY.cellStarted, failed: COPY.cellFailed, onSent },
  );
  const request = (cell) => {
    const source = httpSourceOfColumn(cell?.columnId, columns, sources);
    if (!source) return false;
    if (!target) {
      setTarget({
        columnId: cell.columnId,
        rowId: cell.rowId,
        source,
        unknown: cellOutcomeUnknown(readCell?.(cell)),
      });
    }
    return true;
  };
  const dialog = (
    <HttpActionDialog
      action={action}
      open={Boolean(target)}
      title={COPY.cellTitle}
      lines={
        target
          ? httpCellResendLines({
              url: target.source.url,
              unknownOutcome: target.unknown,
            })
          : []
      }
      items={target ? [target.source.name] : []}
      confirmLabel={COPY.cellConfirm}
      onConfirm={() =>
        target &&
        action.start(retryCellBody(target), () => setTarget(null))
      }
      onClose={() => setTarget(null)}
    />
  );
  return { request, dialog };
}

/** Retry failed rows of the experiment's HTTP sources. */
export function ClassicHttpRetryFailed({ experimentId, detail, onSent }) {
  const [open, setOpen] = useState(false);
  const sources = httpSourcesOf(detail);
  const action = useKeyedHttpAction(
    (body, key) => rerunCells(experimentId, body, { idempotencyKey: key }),
    { started: COPY.failedRowsStarted, failed: COPY.failedRowsFailed, onSent },
  );
  if (!sources.length) return null;
  return (
    <>
      <Button
        variant="outlined"
        disabled={isExperimentPending(detail?.status) || action.busy}
        onClick={() => setOpen(true)}
      >
        {COPY.failedRowsAction}
      </Button>
      <HttpActionDialog
        action={action}
        open={open}
        title={COPY.failedRowsTitle}
        lines={httpFailedRowsLines(sources.length)}
        items={[...new Set(sources.map((s) => s.name))]}
        confirmLabel={COPY.failedRowsConfirm}
        onConfirm={() =>
          action.start(retryFailedRowsBody(sources), () => setOpen(false))
        }
        onClose={() => setOpen(false)}
      />
    </>
  );
}

ClassicHttpRetryFailed.propTypes = {
  experimentId: PropTypes.string.isRequired,
  detail: PropTypes.object,
  onSent: PropTypes.func,
};

/** A message about an action that did not start, ending "Nothing was sent." */
const withNothingSent = (message) => {
  const text = String(message ?? "").trim();
  return `${text}${/[.!?]$/.test(text) ? "" : "."} ${COPY.nothingSent}`;
};

/**
 * How many rows the experiment holds now (its rows read, without search):
 * every one of them gets a request from a new HTTP variant.
 */
export async function readExperimentRowCount(experimentId) {
  const res = await axios.get(
    endpoints.develop.experiment.experimentDetail(experimentId),
    { params: { current_page_index: 0, page_size: 1 } },
  );
  const held = Number(res?.data?.result?.metadata?.total_rows);
  if (!Number.isSafeInteger(held) || held < 0)
    throw new Error(COPY.addRowsReadFailed);
  return held;
}

/** Why the new rows could not be counted, ending "Nothing was sent." */
function addRowsReadFailure(error) {
  // A refusal (no write permission) says why; other failures never show
  // transport or server internals.
  const reason =
    Number(error?.statusCode) === 403
      ? httpRefusalMessage(error, COPY.addRowsReadFailed)
      : getSafeActionErrorMessage(error, COPY.addRowsReadFailed);
  return withNothingSent(reason);
}

/**
 * Add new rows: the source dataset's rows (in the experiment's splits) that
 * the experiment does not include run on its HTTP sources. Before the
 * confirm opens, the server's read-only preview names exactly which of the
 * rows read are new (by row identity, not by comparing totals) and how many
 * requests they send, so the confirm states that number and sends exactly
 * those rows. When nothing can be sent, a message says so and nothing opens.
 */
export function ClassicHttpAddRows({ experimentId, detail, onSent, readRows }) {
  // {body, newRows, requests}: what the confirm states is what it sends.
  const [plan, setPlan] = useState(null);
  const [reading, setReading] = useState(false);
  const sources = httpSourcesOf(detail);
  const splitIds = (detail?.run_scope?.split_ids ?? []).map(String);
  const action = useKeyedHttpAction(
    (body, key) =>
      appendExperimentVariant(experimentId, body, { idempotencyKey: key }),
    { started: COPY.addRowsStarted, failed: COPY.addRowsFailed, onSent },
  );
  if (!sources.length || !detail?.dataset_id) return null;
  // Loaded on use: the Data page does not carry the dataset table readers.
  const read =
    readRows ??
    (async () => {
      const [{ readAllTableRows }, { splitsFilter }] = await Promise.all([
        import("src/sections/develop-detail/Experiment/scopeRows"),
        import("src/api/develop/experiment-create"),
      ]);
      return readAllTableRows(
        endpoints.develop.getDatasetDetail(detail.dataset_id),
        { filters: JSON.stringify(splitsFilter(splitIds)) },
        { limit: HTTP_ROW_LIMIT + 1 },
      );
    });
  const prepare = async () => {
    if (reading || action.busy || plan) return;
    setReading(true);
    try {
      const { rows, complete, total } = await read();
      if (!complete || rows.length > HTTP_ROW_LIMIT || total > HTTP_ROW_LIMIT) {
        enqueueSnackbar(COPY.addRowsTooMany, { variant: "error" });
        return;
      }
      const preview = addRowsPreviewOf(
        await previewHttpAppendRows(
          experimentId,
          rows.map((row) => row?.row_id ?? row?.id),
        ),
      );
      if (!preview) throw new Error(COPY.addRowsReadFailed);
      if (!preview.rowIds.length) {
        enqueueSnackbar(COPY.addRowsNone, { variant: "info" });
        return;
      }
      if (preview.overLimit) {
        enqueueSnackbar(COPY.addRowsTooMany, { variant: "error" });
        return;
      }
      setPlan({
        body: appendHttpRowsBody(preview.rowIds),
        newRows: preview.rowIds.length,
        requests: preview.requests,
      });
    } catch (error) {
      enqueueSnackbar(addRowsReadFailure(error), { variant: "error" });
    } finally {
      setReading(false);
    }
  };
  return (
    <>
      <LoadingButton
        variant="outlined"
        loading={reading}
        disabled={isExperimentPending(detail?.status) || action.busy}
        onClick={prepare}
      >
        {COPY.addRowsAction}
      </LoadingButton>
      <HttpActionDialog
        action={action}
        open={Boolean(plan)}
        title={COPY.addRowsTitle}
        lines={httpAddRowsLines({
          count: sources.length,
          scoped: splitIds.length > 0,
          newRows: plan?.newRows ?? null,
          requests: plan?.requests ?? null,
          selected: ranOnSelectedRows(detail),
        })}
        items={[...new Set(sources.map((s) => s.name))]}
        confirmLabel={COPY.addRowsConfirm}
        onConfirm={() => plan && action.start(plan.body, () => setPlan(null))}
        onClose={() => setPlan(null)}
      />
    </>
  );
}

ClassicHttpAddRows.propTypes = {
  experimentId: PropTypes.string.isRequired,
  detail: PropTypes.object,
  onSent: PropTypes.func,
  readRows: PropTypes.func,
};

/** The Data toolbar's HTTP actions (nothing for other experiments). */
export default function ClassicHttpActions({ experimentId, detail, onSent }) {
  const queryClient = useQueryClient();
  if (!httpSourcesOf(detail).length) return null;
  const sent = () => {
    queryClient.invalidateQueries({ queryKey: ["experiment", experimentId] });
    onSent?.();
  };
  return (
    <>
      <ClassicHttpAddRows experimentId={experimentId} detail={detail} onSent={sent} />
      <ClassicHttpRetryFailed experimentId={experimentId} detail={detail} onSent={sent} />
    </>
  );
}

ClassicHttpActions.propTypes = {
  experimentId: PropTypes.string.isRequired,
  detail: PropTypes.object,
  onSent: PropTypes.func,
};
