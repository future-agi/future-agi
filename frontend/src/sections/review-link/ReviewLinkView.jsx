import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import {
  errorOf,
  reviewLinkKeys,
  submitReviewLinkItem,
  useReviewLink,
  useReviewLinkItem,
} from "src/api/develop/review-links";
import {
  answerProblem,
  asText,
  doneLine,
  errorCopy,
  inviteLine,
  keyAction,
} from "./reviewLinkModel";
import ReviewLinkItemCard from "./ReviewLinkItemCard";

/**
 * An outside reviewer's page (W4-4): no app chrome, no sign-in. One item at
 * a time from the invite's sample, the labels to answer, `Submit and next`
 * (keys 1-9 answer the first label, J / K step through the items), and the
 * expected answer with `Correct` when the review lets reviewers correct it.
 */
export default function ReviewLinkView({ token, startItemId }) {
  const queryClient = useQueryClient();
  const viewQ = useReviewLink(token);
  const view = viewQ.data;
  const [itemId, setItemId] = useState(startItemId ?? null);
  const [done, setDone] = useState(false);
  const [values, setValues] = useState({});
  const [notes, setNotes] = useState({});
  const [correcting, setCorrecting] = useState(false);
  const [corrected, setCorrected] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const current = itemId ?? view?.next_item_id ?? null;
  const itemQ = useReviewLinkItem(token, current);
  const item = itemQ.data;
  const labels = useMemo(
    () => item?.labels ?? view?.queue?.labels ?? [],
    [item, view],
  );
  const allDone =
    view && !view.next_item_id && view.progress.done >= view.progress.total;

  // Reset the form to the reviewer's saved answer when the item (or that
  // answer) changes; keyed by value, not object identity.
  const itemKey = item?.item_id ?? null;
  const mineJson = JSON.stringify(item?.my_answer ?? null);
  useEffect(() => {
    const mine = JSON.parse(mineJson);
    setValues(
      Object.fromEntries(
        (mine?.values ?? []).map((v) => [v.label_id, v.value]),
      ),
    );
    setNotes(
      Object.fromEntries(
        (mine?.values ?? []).map((v) => [v.label_id, v.notes ?? ""]),
      ),
    );
    setCorrected(asText(mine?.corrected_expected?.value ?? ""));
    setCorrecting(Boolean(mine?.corrected_expected));
    setProblem(null);
  }, [itemKey, mineJson]);

  const expectedName = view?.corrections?.column_name;
  const expectedNow = item?.content?.expected?.[expectedName];

  const submit = useCallback(async () => {
    const list = labels.map((l) => ({
      label_id: l.id,
      value: values[l.id] ?? null,
      ...(notes[l.id] ? { notes: notes[l.id] } : {}),
    }));
    const p = answerProblem(labels, list);
    if (p) return setProblem(p);
    setBusy(true);
    try {
      const body = { values: list };
      if (correcting && corrected.trim() && corrected !== asText(expectedNow))
        body.corrected_expected = { value: corrected };
      const out = await submitReviewLinkItem(token, current, body);
      queryClient.invalidateQueries({ queryKey: reviewLinkKeys.view(token) });
      queryClient.invalidateQueries({
        queryKey: reviewLinkKeys.item(token, current),
      });
      if (out?.next_item_id) setItemId(out.next_item_id);
      else setDone(true);
    } catch (e) {
      const { status, message } = errorOf(e);
      setProblem(errorCopy(status, message));
    } finally {
      setBusy(false);
    }
    return undefined;
  }, [
    labels,
    values,
    notes,
    correcting,
    corrected,
    expectedNow,
    token,
    current,
    queryClient,
  ]);

  const ids = useMemo(() => view?.item_ids ?? [], [view]);
  const step = useCallback(
    (delta) => {
      const i = ids.indexOf(current);
      const next = ids[i + delta];
      if (next) {
        setDone(false);
        setItemId(next);
      }
    },
    [ids, current],
  );

  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (
        t?.tagName === "TEXTAREA" ||
        t?.tagName === "INPUT" ||
        t?.isContentEditable
      )
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const first = labels[0];
      const act = keyAction(
        e.key.toLowerCase(),
        first,
        item?.content?.candidates,
      );
      if (act?.move) step(act.move);
      else if (act?.value)
        setValues((cur) => ({ ...cur, [first.id]: act.value }));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [labels, item, step]);

  if (viewQ.isLoading)
    return (
      <Stack alignItems="center" sx={{ py: 10 }}>
        <CircularProgress size={24} />
      </Stack>
    );
  if (viewQ.isError) {
    const { status, message } = errorOf(viewQ.error);
    return (
      <Stack
        alignItems="center"
        spacing={1.5}
        sx={{ py: 12 }}
        data-testid="review-link-error"
      >
        <Iconify icon="mdi:link-variant-off" width={40} color="text.disabled" />
        <Typography variant="h6">{errorCopy(status, message)}</Typography>
      </Stack>
    );
  }
  if (!view) return null;
  const total = view.progress.total;
  const header = (
    <Stack spacing={0.5} sx={{ mb: 2 }}>
      <Typography variant="h5" data-testid="review-link-title">
        Review for {view.org_name} · {view.dataset_name}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        {inviteLine({
          invitedBy: view.invited_by?.name,
          total,
          expiresAt: view.expires_at,
        })}
      </Typography>
    </Stack>
  );
  if (done || (allDone && !itemId))
    return (
      <Box sx={{ maxWidth: 860, mx: "auto", px: 3, py: 4 }}>
        {header}
        <Stack
          alignItems="center"
          spacing={1.5}
          sx={{ py: 8 }}
          data-testid="review-link-done"
        >
          <Iconify
            icon="eva:checkmark-circle-2-fill"
            width={56}
            color="success.main"
          />
          <Typography variant="h6">
            {doneLine(view.progress.done || total)}
          </Typography>
          {ids.length ? (
            <Button
              size="small"
              onClick={() => {
                setDone(false);
                setItemId(ids[0]);
              }}
            >
              Look at my answers again
            </Button>
          ) : null}
        </Stack>
      </Box>
    );
  const itemError = itemQ.isError ? errorOf(itemQ.error) : null;
  const content = item?.content;
  return (
    <Box sx={{ maxWidth: 860, mx: "auto", px: 3, py: 4 }}>
      {header}
      {view.queue?.instructions ? (
        <Alert severity="info" icon={false} sx={{ mb: 2 }}>
          {view.queue.instructions}
        </Alert>
      ) : null}
      <Stack direction="row" alignItems="center" gap={1.5} sx={{ mb: 1.5 }}>
        <Typography variant="subtitle2" data-testid="review-link-position">
          {item ? `${item.index} of ${item.total}` : ""}
        </Typography>
        <LinearProgress
          variant="determinate"
          value={total ? (view.progress.done / total) * 100 : 0}
          sx={{ flex: 1, height: 6, borderRadius: 3 }}
        />
        <Typography variant="caption" color="text.secondary">
          {view.progress.done} answered · J / K to move
        </Typography>
      </Stack>
      {itemError ? (
        <Alert severity="warning" data-testid="review-link-item-error">
          {errorCopy(itemError.status, itemError.message)}
        </Alert>
      ) : !item ? (
        <Stack alignItems="center" sx={{ py: 6 }}>
          <CircularProgress size={20} />
        </Stack>
      ) : (
        <Stack spacing={2} data-testid="review-link-item">
          <ReviewLinkItemCard
            content={content}
            labels={labels}
            values={values}
            setValues={setValues}
            notes={notes}
            setNotes={setNotes}
            expectedName={expectedName}
            correction={{
              correcting,
              corrected,
              setCorrecting,
              setCorrected,
            }}
          />
          {problem ? (
            <Alert severity="error" sx={{ py: 0.25 }}>
              {problem}
            </Alert>
          ) : null}
          <Stack direction="row" justifyContent="flex-end" gap={1}>
            <Button
              size="small"
              color="inherit"
              disabled={ids.indexOf(current) <= 0}
              onClick={() => step(-1)}
            >
              Previous (K)
            </Button>
            <Button
              variant="contained"
              disabled={busy}
              onClick={submit}
              data-testid="review-link-submit"
            >
              Submit and next
            </Button>
          </Stack>
        </Stack>
      )}
    </Box>
  );
}

ReviewLinkView.propTypes = {
  token: PropTypes.string.isRequired,
  // `?item=` on the link (opens that item when it is in the sample)
  startItemId: PropTypes.string,
};
