import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  MenuItem,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  PROVIDER_LABELS,
  definitionOf,
  sourceKindOf,
} from "../_mock/agentRegistry";
import {
  agentStatusOf,
  disconnectReasonFor,
  fixDepthOf,
  platformTestingOf,
  releaseDetectionOf,
} from "../_mock/rsiModel";
import { InfoTip, KvRow, PanelCard } from "./AgentBits";
import { ago, plural } from "./agentViewUtils";
import { deleteBlockOf } from "./agentDelete";
import { ReinstallGithubButton } from "./AgentProfileStrip";
import { useDemoMode } from "./demoMode";

/**
 * What can happen to the agent next — reconnect, archive or restore, delete.
 * Its status sits in the page header; fix depth on Constraints; proof under
 * How we test it. On the Connections tab.
 */
export function LifecycleCard({
  agent,
  onSaveValues,
  onArchive,
  onDelete,
  onMove,
}) {
  const status = agentStatusOf(agent);
  /* Deleted only once it has no environments left. */
  const blocked = deleteBlockOf(agent);
  const archived = status.status === "archived";
  const disconnected = status.status === "disconnected";
  const reconnect = () =>
    onSaveValues({ disconnected: null }, `${agent.name} is connected again.`);

  /* A deleted agent is kept for its history only — nothing left to do to it. */
  if (status.status === "deleted") {
    return (
      <PanelCard bare>
        <KvRow label="Deleted" first hint={status.at ? ago(status.at) : null}>
          <Typography sx={{ typography: "s2" }}>{status.reason}</Typography>
        </KvRow>
      </PanelCard>
    );
  }

  return (
    <PanelCard bare>
      {status.reason && (
        <KvRow
          label={archived ? "Archived" : "Disconnected"}
          first
          hint={status.at ? ago(status.at) : null}
        >
          <Typography
            sx={{
              typography: "s2",
              color: disconnected ? "status.danger.fg" : "text.primary",
            }}
          >
            {status.reason}
          </Typography>
        </KvRow>
      )}
      <Stack
        spacing={0}
        sx={{
          ...(status.reason && {
            borderTop: "1px solid",
            borderColor: "divider",
          }),
        }}
      >
        {disconnected && (
          <ActionRow
            title="Reconnect"
            body={
              status.cause === "app"
                ? "Reinstall the GitHub App on the repository"
                : "Once the key is replaced"
            }
            hint={
              status.cause === "app"
                ? "Reinstall the GitHub App on the repository, then it reconnects on its own."
                : "Once the key's reference is replaced (or the agent exists again), mark it connected."
            }
          >
            {status.cause === "app" ? (
              <ReinstallGithubButton agent={agent} />
            ) : (
              <Button
                size="small"
                variant="outlined"
                onClick={reconnect}
                sx={btnSx}
              >
                Reconnect
              </Button>
            )}
          </ActionRow>
        )}
        {/* Gone for good on its platform: the environments testing it follow a Move. */}
        {disconnected && onMove && (agent.usedIn || []).length > 0 && (
          <ActionRow
            title="Replace it"
            body={`${agent.usedIn.length} environment${agent.usedIn.length === 1 ? " tests" : "s test"} this agent — no new runs until it's back`}
            hint={`Point this agent at its replacement (a new ${status.cause === "app" ? "repository" : "assistant or account"}) and ${
              (agent.usedIn || [])
                .map((u) => u.envName)
                .filter(Boolean)
                .slice(0, 3)
                .join(", ") || "its environments"
            } follow it. Past runs and results stay as they were.`}
          >
            <Button size="small" variant="outlined" onClick={onMove} sx={btnSx}>
              Move…
            </Button>
          </ActionRow>
        )}
        <ActionRow
          title={archived ? "Restore" : "Archive"}
          body={
            archived
              ? "Runs and improvements can start again"
              : "Stops new runs and improvements"
          }
          hint={
            archived
              ? "Test copies are made again on the next run."
              : "Test copies we created on the platform are removed. Versions, environments, runs and proofs stay. You can restore it."
          }
        >
          <Button
            size="small"
            variant="outlined"
            onClick={onArchive}
            sx={btnSx}
          >
            {archived ? "Restore" : "Archive"}
          </Button>
        </ActionRow>
        <ActionRow
          title="Delete"
          body={
            blocked
              ? `Not while it has ${plural(agent.usedIn.length, "environment")}`
              : "Takes it off Agents and every picker"
          }
          hint={
            blocked ||
            "Its versions, and the runs, improvements and proofs that name it, are kept — they still open its history."
          }
        >
          <Tooltip arrow title={blocked || ""}>
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={!!blocked}
                onClick={onDelete}
                sx={{
                  ...btnSx,
                  color: "status.danger.fg",
                  borderColor: "status.danger.border",
                }}
              >
                Delete
              </Button>
            </span>
          </Tooltip>
        </ActionRow>
      </Stack>
    </PanelCard>
  );
}
LifecycleCard.propTypes = {
  agent: PropTypes.object.isRequired,
  onSaveValues: PropTypes.func.isRequired,
  onArchive: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  /* Opens Move… — the replacement path, which its environments follow. */
  onMove: PropTypes.func,
};

/**
 * Staging only (?demo=1): make the states the product reaches on its own —
 * a revoked key, an edit made outside Future AGI — so they can be shown.
 */
export function PrototypeControlsCard({ agent, onSaveValues }) {
  const demo = useDemoMode();
  const status = agentStatusOf(agent);
  if (!demo || status.status === "archived" || status.status === "disconnected")
    return null;
  const isPlatform =
    sourceKindOf(agent) === "platform" ||
    (agent.values?.provider && agent.values?.agentId);
  const pending = !!agent.values?.pendingOutsideChange?.at;
  return (
    <PanelCard bare>
      <ActionRow
        title="Revoked key"
        body="Shows the agent as Disconnected"
        hint="A revoked key, removed GitHub App or deleted agent shows Disconnected, with the reason."
      >
        <Button
          size="small"
          variant="outlined"
          disabled={!isPlatform}
          sx={btnSx}
          onClick={() =>
            onSaveValues(
              {
                disconnected: {
                  cause: "key",
                  at: new Date().toISOString(),
                  reason: disconnectReasonFor(agent, "key"),
                },
              },
              `${agent.name} is disconnected — runs and improvements stop until it's reconnected.`,
            )
          }
        >
          Revoke key
        </Button>
      </ActionRow>
      <ActionRow
        title="Edit made outside Future AGI"
        body={
          pending ? "An edit is waiting" : "Becomes an outside-change version"
        }
        hint={
          pending
            ? "The next Sync saves it as a new version marked as an outside change."
            : "A change made outside Future AGI becomes a version marked as an outside change."
        }
      >
        <Button
          size="small"
          variant="outlined"
          sx={btnSx}
          disabled={pending}
          onClick={() =>
            onSaveValues(
              { pendingOutsideChange: { at: new Date().toISOString() } },
              "An outside edit is waiting — the next improvement syncs it.",
            )
          }
        >
          Simulate edit
        </Button>
      </ActionRow>
    </PanelCard>
  );
}
PrototypeControlsCard.propTypes = {
  agent: PropTypes.object.isRequired,
  onSaveValues: PropTypes.func.isRequired,
};

const btnSx = { flexShrink: 0 };

function ActionRow({ title, body, hint, children }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{
        px: 2,
        py: 1.125,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {body}
          <InfoTip text={hint} />
        </Typography>
      </Box>
      {children}
    </Stack>
  );
}
ActionRow.propTypes = {
  title: PropTypes.node,
  body: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};

/**
 * Archive (or restore) — says exactly what is removed and what stays.
 *   copies — names of the test copies we made on the platform
 */
export function ArchiveDialog({
  open,
  agent,
  copies = [],
  onClose,
  onConfirm,
}) {
  const archived = agentStatusOf(agent).status === "archived";
  const provider = PROVIDER_LABELS[agent.values?.provider];
  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          {archived ? `Restore ${agent.name}?` : `Archive ${agent.name}?`}
        </Typography>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {archived
            ? "Runs and improvements can start again. Its versions, environments, runs and proofs are as they were."
            : `New runs and improvements stop. Its ${[plural(agent.versions.length, "version"), agent.usedIn.length ? plural(agent.usedIn.length, "environment") : null, "runs and proofs"].filter(Boolean).join(", ")} stay. Restore it any time.`}
        </Typography>
        {!archived && copies.length > 0 && (
          <Box
            sx={{
              mt: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            <Typography
              sx={{
                px: 1.5,
                py: 0.75,
                typography: "s3",
                fontWeight: 600,
                color: "text.subtitle",
                bgcolor: "background.neutral",
              }}
            >
              {`Removed from ${provider || "the platform"}`}
            </Typography>
            {copies.map((c) => (
              <Typography
                key={c}
                sx={{
                  px: 1.5,
                  py: 0.625,
                  typography: "s3",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                {c}
              </Typography>
            ))}
          </Box>
        )}
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={onConfirm}
        >
          {archived ? "Restore" : "Archive"}
        </Button>
      </Stack>
    </Dialog>
  );
}
ArchiveDialog.propTypes = {
  open: PropTypes.bool,
  agent: PropTypes.object.isRequired,
  copies: PropTypes.array,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};

/**
 * Release detection: what "released" means for this agent and — for code —
 * the release branch or tag we watch. Re-reads aren't a setting; the Sync
 * card says when they happen.
 *   values.releaseDetection = { watch, ref }
 */
export function ReleaseDetectionCard({ agent, onSaveValues }) {
  const kind = sourceKindOf(agent);
  /* Code in a repository is released by a merge on the branch or tag we
     watch; an upload has no branch — it's released when the patched code is
     uploaded (RSI PRD, Versions and releases). */
  const branchWatched =
    kind === "repo" || (kind === "framework" && !!definitionOf(agent).repoUrl);
  const platform = platformTestingOf(agent);
  const rd = releaseDetectionOf(agent);
  const fix = fixDepthOf(agent);
  const save = (patch, msg) =>
    onSaveValues({ releaseDetection: { ...rd, ...patch } }, msg);
  const releasedWhen =
    platform?.released ||
    (branchWatched
      ? `Released when the change is merged into ${rd.watch === "tag" ? `the commit tagged ${rd.ref}` : rd.ref}.`
      : null) ||
    (kind === "upload" ? "Released when you upload the patched code." : null) ||
    (kind === "builtin"
      ? "Released when a workspace admin makes it the active version."
      : null) ||
    (fix.depth === "suggestion"
      ? "Released when you apply the suggested fix and mark the new version released."
      : fix.depth === "coding-agent"
        ? "Released when you deploy the change and mark the new version released."
        : "Released when a workspace admin makes it the active version.");

  return (
    <PanelCard title="Release detection">
      {/* The first clause on the row; the whole rule behind its ⓘ. */}
      <KvRow
        label="Released when"
        first
        hint={
          releasedWhen.split(/[,.—]/)[0].trim().length <
          releasedWhen.replace(/\.$/, "").length
            ? releasedWhen
            : null
        }
      >
        {releasedWhen
          .split(/[,.—]/)[0]
          .trim()
          .replace(/^Released when /, "When ")}
      </KvRow>
      {branchWatched && (
        <KvRow label="We watch" align="center">
          <Stack direction="row" spacing={1} alignItems="center">
            <Select
              size="small"
              value={rd.watch}
              onChange={(e) =>
                save({
                  watch: e.target.value,
                  ref: e.target.value === "tag" ? "prod" : "main",
                })
              }
              sx={{
                height: 28,
                typography: "s2",
                "& .MuiSelect-select": { typography: "s2", py: 0.25 },
              }}
            >
              <MenuItem value="branch" sx={{ typography: "s2" }}>
                Release branch
              </MenuItem>
              <MenuItem value="tag" sx={{ typography: "s2" }}>
                Tag
              </MenuItem>
            </Select>
            <TextField
              size="small"
              defaultValue={rd.ref}
              key={`${rd.watch}-${rd.ref}`}
              onBlur={(e) =>
                e.target.value.trim() &&
                e.target.value.trim() !== rd.ref &&
                save(
                  { ref: e.target.value.trim() },
                  `Watching ${e.target.value.trim()}.`,
                )
              }
              InputProps={{
                sx: {
                  height: 28,
                  typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace",
                  width: 140,
                },
              }}
            />
          </Stack>
        </KvRow>
      )}
    </PanelCard>
  );
}
ReleaseDetectionCard.propTypes = {
  agent: PropTypes.object.isRequired,
  onSaveValues: PropTypes.func.isRequired,
};
