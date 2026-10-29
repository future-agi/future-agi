import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CopyCommandButton from "../components/CopyCommandButton";
import { useEnvState } from "../store";
import { useRoleGates } from "../_mock/previewRole";
import {
  approvalCli,
  approvalOf,
  decideApprovalPatch,
} from "../_mock/approvals";

const KIND = {
  ask: { icon: "solar:user-linear" },
  request: { icon: "solar:arrow-right-linear" },
  attempt: { icon: "solar:shield-cross-linear", tone: "status.danger.fg" },
  wrong: { icon: "solar:danger-triangle-linear", tone: "status.warning.fg" },
  wait: { icon: "solar:clock-circle-linear" },
  decision: { icon: "solar:check-read-linear" },
  commit: { icon: "solar:database-linear" },
  reply: { icon: "solar:chat-round-line-linear" },
};
const RESULT = {
  applied: "Applied",
  blocked: "Blocked",
  ignored: "Ignored — applied once",
  retried: "Retried",
};

/**
 * One conversation's approval (roadmap actors.md, Week 2): who asked whom,
 * the wait, the decision and what was written — attempted and committed
 * kept apart, an unauthorised "approved" named for what it is, and each
 * write in an action log that says whether a repeat was applied or
 * ignored. While it's waiting, a person who may decide can approve or
 * reject as the approver.
 */
export default function ApprovalTimeline({ env, task }) {
  const { envState, patch } = useEnvState(env.id);
  const gates = useRoleGates();
  const a = approvalOf({ env, envState, task });
  if (!a) return null;
  const decide = (decision) =>
    patch(decideApprovalPatch(envState, task.id, decision));

  return (
    <Stack spacing={1.5} sx={{ p: 2 }}>
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {`${a.approver.name} approves ${a.approver.approvalScope || "requests above the limit"}. ${a.attempted ? "An unauthorised write was attempted" : "No write was attempted early"} · ${a.committed} committed.`}
      </Typography>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {a.events.map((e, i) => {
          const k = KIND[e.kind] || KIND.reply;
          return (
            <Stack
              key={`${e.kind}-${i}`}
              direction="row"
              spacing={1.25}
              sx={{ px: 1.5, py: 1, ...(e.nested && { pl: 4 }) }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 52,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                  pt: "1px",
                }}
              >
                {e.at >= 60
                  ? `${Math.round(e.at / 60)} min`
                  : `${e.at.toFixed(1)} s`}
              </Typography>
              <Iconify
                icon={k.icon}
                width={14}
                sx={{
                  color: k.tone || "text.secondary",
                  mt: "3px",
                  flexShrink: 0,
                }}
              />
              <Box sx={{ minWidth: 0 }}>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {e.who}
                </Typography>
                <Typography
                  sx={{ typography: "s2", color: k.tone || "text.primary" }}
                >
                  {e.text}
                </Typography>
              </Box>
            </Stack>
          );
        })}
      </Box>

      {a.pending && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            p: 1.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          <Typography
            sx={{ typography: "s2", flex: 1 }}
          >{`Waiting for approval — decide as ${a.approver.name}`}</Typography>
          <Tooltip
            arrow
            title={
              gates.canEdit
                ? ""
                : "Viewers can't decide — ask a workspace admin for Member access"
            }
          >
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={!gates.canEdit}
                onClick={() => decide("rejected")}
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Reject
              </Button>
            </span>
          </Tooltip>
          <Tooltip
            arrow
            title={
              gates.canEdit
                ? ""
                : "Viewers can't decide — ask a workspace admin for Member access"
            }
          >
            <span>
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!gates.canEdit}
                onClick={() => decide("approved")}
              >
                Approve
              </Button>
            </span>
          </Tooltip>
        </Stack>
      )}

      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Checks
        </Typography>
        {a.checks.map((c) => (
          <Stack
            key={c.label}
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ py: 0.25 }}
          >
            <Iconify
              icon={
                c.pass
                  ? "solar:check-circle-linear"
                  : "solar:close-circle-linear"
              }
              width={14}
              sx={{ color: c.pass ? "text.secondary" : "status.danger.fg" }}
            />
            <Typography sx={{ typography: "s2" }}>{c.label}</Typography>
          </Stack>
        ))}
      </Box>

      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
          Action log
        </Typography>
        {a.log.length ? (
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          >
            {a.log.map((l, i) => (
              <Stack
                key={i}
                direction="row"
                spacing={1.5}
                sx={{
                  px: 1.5,
                  py: 0.75,
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    fontFamily: "ui-monospace, Menlo, monospace",
                    width: 140,
                    flexShrink: 0,
                  }}
                >
                  {l.tool}
                </Typography>
                <Typography sx={{ typography: "s3", width: 64, flexShrink: 0 }}>
                  {l.by}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color:
                      l.result === "blocked"
                        ? "status.danger.fg"
                        : "text.primary",
                    width: 150,
                    flexShrink: 0,
                  }}
                >
                  {RESULT[l.result]}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {l.note}
                </Typography>
              </Stack>
            ))}
          </Box>
        ) : (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Nothing was written — the record is unchanged.
          </Typography>
        )}
      </Box>

      <Stack direction="row">
        <CopyCommandButton
          label="Copy approval command"
          command={
            a.pending
              ? approvalCli.decide(`apr_${task.id.slice(-6)}`)
              : approvalCli.list(task.runId)
          }
        />
      </Stack>
    </Stack>
  );
}

ApprovalTimeline.propTypes = {
  env: PropTypes.object.isRequired,
  task: PropTypes.object.isRequired,
};
