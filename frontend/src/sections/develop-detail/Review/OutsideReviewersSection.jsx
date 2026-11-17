import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  Collapse,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import notify from "src/sections/common/ux/toast";
import { useAuthContext } from "src/auth/hooks";
import { useResultExampleIds } from "src/api/develop/review";
import {
  approveInvite,
  inviteKeys,
  revokeInvite,
  sendInvites,
  useQueueInvites,
  useReviewLinkPolicy,
} from "src/api/develop/review-links";
import InviteForm from "./InviteForm";
import InvitesTable from "./InvitesTable";
import {
  filterLabel,
  initialInvite,
  inviteBody,
  inviteProblem,
  samplePills,
} from "./inviteModel";

const errorText = (e, fallback) =>
  (typeof e?.result === "string" && e.result) || fallback;

const intro = (org) =>
  `Create review links for people outside ${org}. No email is sent: each person gets their own link, listed in the review summary to copy and share. They need no seat or sign-in, see one item at a time and only the items you sample.`;

function useStartFilters({
  datasetId,
  runs,
  baseline,
  mode,
  names,
  metric,
  rowFilters,
  selectedIds,
  total,
  enabled,
}) {
  const pills = (key) =>
    samplePills(key, {
      mode,
      metric,
      runId: runs?.[0],
      runA: runs?.[0],
      runB: runs?.[1],
    });
  const keys = mode === "pairwise" ? ["failing", "disagree"] : ["failing"];
  const qs = {
    failing: useResultExampleIds(
      datasetId,
      {
        runs,
        baseline,
        filters: [...(rowFilters ?? []), ...(pills("failing") ?? [])],
        sort: "order",
      },
      { enabled: enabled && Boolean(pills("failing")?.length) },
    ),
    disagree: useResultExampleIds(
      datasetId,
      {
        runs,
        baseline,
        filters: [...(rowFilters ?? []), ...(pills("disagree") ?? [])],
        sort: "order",
      },
      {
        enabled:
          enabled && mode === "pairwise" && Boolean(pills("disagree")?.length),
      },
    ),
  };
  const countOf = (data) => {
    if (!data) return null;
    if (!selectedIds) return data.total;
    const pick = new Set(selectedIds);
    return data.example_ids.filter((id) => pick.has(id)).length;
  };
  return [
    { key: "all", label: filterLabel("all", names), count: total ?? 0 },
    ...keys
      .filter((k) => pills(k)?.length)
      .map((k) => ({
        key: k,
        label: filterLabel(k, names),
        count: countOf(qs[k].data),
      }))
      .filter((f) => f.count != null),
  ];
}

/**
 * Start review › Outside reviewers (W4-4): a switch, then the invite form
 * over the rows the new queue will hold. The dialog sends the invites
 * (`sendOutsideInvites`) right after it creates the queue.
 */
export function OutsideReviewersStart({
  value,
  onChange,
  datasetId,
  runs,
  baseline,
  mode,
  names,
  metric,
  rowFilters,
  selectedIds,
  total,
}) {
  const { role } = useAuthContext();
  const viewer = role === "workspace_viewer";
  const policyQ = useReviewLinkPolicy({ enabled: !viewer });
  const policy = policyQ.data;
  const filters = useStartFilters({
    datasetId,
    runs,
    baseline,
    mode,
    names,
    metric,
    rowFilters,
    selectedIds,
    total,
    enabled: Boolean(value?.on),
  });
  useEffect(() => {
    if (policy && !value) onChange({ on: false, ...initialInvite(policy) });
  }, [policy, value, onChange]);
  if (viewer || !policy || !value) return null;
  return (
    <Stack spacing={1} data-testid="outside-reviewers-start">
      <Stack direction="row" alignItems="center" gap={1}>
        <Switch
          size="small"
          checked={value.on}
          onChange={(e) => onChange({ ...value, on: e.target.checked })}
          inputProps={{ "aria-label": "Outside reviewers" }}
        />
        <Typography typography="s1" fontWeight="fontWeightMedium">
          Outside reviewers
        </Typography>
      </Stack>
      <Typography typography="s2" color="text.secondary">
        {intro(policy.org_name)}
      </Typography>
      {value.on ? (
        <InviteForm
          policy={policy}
          filters={filters}
          value={value}
          onChange={onChange}
        />
      ) : null}
    </Stack>
  );
}

OutsideReviewersStart.propTypes = {
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  runs: PropTypes.array,
  baseline: PropTypes.string,
  mode: PropTypes.string,
  names: PropTypes.object,
  metric: PropTypes.string,
  rowFilters: PropTypes.array,
  selectedIds: PropTypes.array,
  total: PropTypes.number,
};

/**
 * Review summary › Outside reviewers (W4-4): who was invited, their status
 * and progress, Copy link and Revoke; admins approve waiting invites;
 * members and admins invite more people. Absent for viewers.
 */
export default function OutsideReviewersSection({ queueId }) {
  const queryClient = useQueryClient();
  const q = useQueueInvites(queueId);
  const data = q.data;
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const policy = data?.policy;
  const filters = useMemo(() => data?.sampling?.filters ?? [], [data]);
  useEffect(() => {
    if (policy && !form) setForm(initialInvite(policy));
  }, [policy, form]);
  if (!data?.can_invite) return null;
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: inviteKeys.queue(queueId) });
  const pool =
    filters.find((f) => f.key === (form?.filter ?? "all"))?.count ?? 0;
  const run = async (fn, okText) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
      if (okText)
        notify(okText, { variant: "success", key: "outside-invites" });
      return true;
    } catch (e) {
      setError(errorText(e, "That didn't work. Try again."));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const send = async () => {
    const problem = inviteProblem(form, pool);
    if (problem) return setError(problem);
    let out;
    const done = await run(async () => {
      out = await sendInvites(
        queueId,
        inviteBody(form, { metric: data.sampling?.metric }),
      );
    });
    if (done) {
      const n = out?.invites?.length ?? 0;
      notify(
        out?.pending_approval
          ? `${n} ${n === 1 ? "invite waits" : "invites wait"} for an admin to approve`
          : `Created ${n} review ${n === 1 ? "link" : "links"} · copy them from the list below`,
        { variant: "success", key: "outside-invites" },
      );
      setForm(initialInvite(policy));
      setOpen(false);
    }
    return undefined;
  };
  const waiting = data.invites.filter((i) => i.status === "pending_approval");
  return (
    <Stack spacing={1.25} data-testid="outside-reviewers">
      <Stack direction="row" alignItems="center" gap={1}>
        <Iconify icon="mdi:account-arrow-right-outline" width={18} />
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Outside reviewers
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Close" : "Create review links"}
        </Button>
      </Stack>
      {waiting.length && data.can_approve ? (
        <Alert
          severity="info"
          sx={{ py: 0 }}
          action={
            <Button
              size="small"
              color="inherit"
              disabled={busy}
              onClick={() =>
                run(
                  () =>
                    Promise.all(
                      waiting.map((i) => approveInvite(queueId, i.id)),
                    ),
                  `Approved ${waiting.length} ${waiting.length === 1 ? "invite" : "invites"}`,
                )
              }
            >
              Approve all
            </Button>
          }
        >
          {waiting.length}{" "}
          {waiting.length === 1 ? "invite waits" : "invites wait"} for approval
        </Alert>
      ) : null}
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={1.25}
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
          }}
        >
          <Typography typography="s2" color="text.secondary">
            {intro(policy?.org_name)}
          </Typography>
          {form ? (
            <InviteForm
              policy={policy}
              filters={filters}
              value={form}
              onChange={setForm}
            />
          ) : null}
          <Stack direction="row" justifyContent="flex-end">
            <Button
              size="small"
              variant="contained"
              disabled={busy}
              onClick={send}
            >
              Create links
            </Button>
          </Stack>
        </Stack>
      </Collapse>
      {error ? (
        <Alert severity="error" sx={{ py: 0 }}>
          {error}
        </Alert>
      ) : null}
      {data.invites.length ? (
        <InvitesTable
          invites={data.invites}
          canApprove={Boolean(data.can_approve)}
          busy={busy}
          onApprove={(inv) =>
            run(() => approveInvite(queueId, inv.id), `Approved ${inv.email}`)
          }
          onRevoke={(inv) =>
            run(
              () => revokeInvite(queueId, inv.id),
              `Revoked the link for ${inv.email}`,
            )
          }
        />
      ) : null}
    </Stack>
  );
}

OutsideReviewersSection.propTypes = { queueId: PropTypes.string.isRequired };
