import PropTypes from "prop-types";
import { useSnackbar } from "notistack";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import { nextAgentVersion } from "../_mock/versions";
import { releaseDetectionOf } from "../_mock/rsiModel";
import { definitionOf } from "../_mock/agentRegistry";
import { draftVersionOf } from "../agents/versionRelease";
import {
  GIT_HOST_META,
  postSlack,
  prCheckSummary,
  reviewNounOf,
  setTracing,
  updatePr,
  useIntegrations,
} from "../_mock/integrations";
import {
  hasVerifyCheck,
  headOf,
  isPending,
  latestCheck,
  checkStandingOf,
  statusOf,
  useNow,
} from "../_mock/prChecks";
import { PrStatus } from "./GithubIntegrationPage";
import PrVerifyCheck from "./PrVerifyCheck";

const MONO = "ui-monospace, Menlo, monospace";
const CHANGED = {
  prompt: "Prompt",
  code: "Tool code",
  orchestration: "Orchestration",
  guardrail: "Guardrail",
};

/**
 * Where a fix stands once it has left the product: open on GitHub (with our
 * simulation check on it) → merged. It opens as soon as the person opens it —
 * no approval step of ours; their GitHub reviewers decide. Merging
 * is the change actually shipping, so it saves the agent version from that
 * commit and asks for the re-run that proves it. A PR an improvement opened
 * already recorded a Draft version when it opened; merging releases that
 * one rather than minting a second. Otherwise this card mints it — the only
 * place it does. The re-run tests that version; it doesn't mint another.
 *
 * Merging releases the change only when the PR's base is the release branch
 * the agent's Connections tab watches (main by default). Merged anywhere else
 * it lands as the Draft version, not released — and says so. With a tag
 * watched instead, it's released once that tag points at it.
 *
 * An improvement's PR also carries the required "Future AGI · verify" check:
 * every push re-runs the frozen scenarios on the new head commit and holds it
 * to the proof, and merge stays blocked while that fails.
 *
 * The prototype has no GitHub on the other end, so merge, close and pushes
 * are buttons here; in the product they arrive from the GitHub webhook.
 */
export default function PrCard({ prId, env, envState, onRerun, onMerged }) {
  const { prs } = useIntegrations();
  const { agents, recordAgentVersion, setAgentValues } = useConnectedAgents();
  const { dispatch } = useSimStore();
  const { enqueueSnackbar } = useSnackbar();
  const pr = prs.find((p) => p.id === prId);
  /* Ticks only while a verify run is queued or running. */
  const verify = hasVerifyCheck(pr);
  const now = useNow(
    verify && isPending(statusOf(latestCheck(pr), Date.now())),
  );

  if (!pr) return null;

  const telemetry = pr.kind === "telemetry";
  /* GitLab calls it a merge request (!142); GitHub and Bitbucket a pull request (#142). */
  const review = reviewNounOf(pr.host);
  const hostLabel = GIT_HOST_META[pr.host]?.label || "GitHub";
  const hostIcon = GIT_HOST_META[pr.host]?.icon || "mdi:github";
  const ref = `${review.ref}${pr.number}`;
  const standing = verify ? checkStandingOf(pr, now) : null;
  /* What the agent watches for releases, and where this PR merges. A coding
     agent's PR doesn't name its base; it targets the repository's branch. */
  const watched = releaseDetectionOf(envState?.agent);
  const base = pr.base || definitionOf(envState?.agent)?.ref || "main";
  const merge = () => {
    const mergedAt = new Date().toISOString();
    const releases = watched.watch === "branch" && base === watched.ref;
    updatePr(pr.id, {
      status: "merged",
      mergedAt,
      ...(!telemetry && {
        mergedInto: base,
        released: releases,
        watch: watched.watch,
        watchRef: watched.ref,
      }),
    });
    /* Tracing changes where the agent reports, not what it does — so it
       turns tracing on and leaves the versions alone. */
    if (telemetry) {
      setTracing(pr.agentKey, {
        on: true,
        since: mergedAt,
        project: pr.project,
        framework: pr.framework,
        prNumber: pr.number,
        repo: pr.repo,
      });
      /* The agent's own record of the PR agrees — its Commits list shows the tracing commit merged. */
      const owner = (agents || []).find((a) => a.key === pr.agentKey);
      if (owner?.values?.tracingPr?.id === pr.id)
        setAgentValues(pr.agentKey, {
          tracingPr: { ...owner.values.tracingPr, mergedAt },
        });
      enqueueSnackbar(
        "Merged — tracing is on. Traces show up in Observe once this deploys.",
        { variant: "success" },
      );
      postSlack({
        event: "pr",
        title: `Tracing ${review.short} merged · ${pr.repo} ${ref}`,
        lines: [
          `${pr.agentName} now sends traces to Future AGI (project ${pr.project}).`,
        ],
        actions: ["Open in Observe"],
        links: { "Open in Observe": paths.dashboard.observe },
      });
      return;
    }
    /* The Draft version recorded when this PR opened — released, not re-minted. */
    const draft =
      envState?.agent && pr.agentKey
        ? draftVersionOf(envState, pr.handoffKey)
        : null;
    if (draft) {
      const landed = {
        ...(releases
          ? { status: "released", releasedAt: mergedAt }
          : { landedAt: mergedAt }),
        origin: "pr",
        prNumber: pr.number,
        note: `Merged ${review.short} ${ref} — ${pr.title}`,
        changes: pr.files.map((f) => ({
          kind: f.kind === "prompt" ? "instructions" : "tool",
          title: `${CHANGED[f.kind] || "Code"} changed · ${f.path}`,
          detail: f.title,
        })),
      };
      dispatch({
        type: "updateAgentVersion",
        key: pr.agentKey,
        label: draft.label,
        patch: landed,
        ...(releases && { activateEnvId: env?.id }),
      });
      updatePr(pr.id, { version: draft.label });
      onMerged?.({ ...draft, ...landed }, { released: releases });
      enqueueSnackbar(
        releases
          ? `Merged into ${base} — Draft ${draft.label} is now released${env ? ", and this environment tests it" : ""}`
          : `Merged into ${base} — ${notReleasedWhy(watched)}. Draft ${draft.label} stays a draft.`,
        { variant: releases ? "success" : "info" },
      );
    } else if (envState?.agent && pr.agentKey) {
      const version = {
        /* The diff and the run it came from travel with the version, when the
           PR was opened from one. */
        ...nextAgentVersion(envState, {
          note: `Merged ${review.short} ${ref} — ${pr.title}`,
          applied: pr.applied || [],
          fromRunId: pr.fromRunId || null,
          basedOnVersion: pr.basedOnVersion || null,
        }),
        origin: "pr",
        prNumber: pr.number,
        connectedAt: mergedAt,
        changes: pr.files.map((f) => ({
          kind: f.kind === "prompt" ? "instructions" : "tool",
          title: `${CHANGED[f.kind] || "Code"} changed · ${f.path}`,
          detail: f.title,
        })),
      };
      recordAgentVersion(pr.agentKey, version, env?.id);
      updatePr(pr.id, { version: version.label });
      onMerged?.(version, { released: releases });
      enqueueSnackbar(
        !releases
          ? `Merged into ${base} — ${notReleasedWhy(watched)}. Saved as ${version.label}.`
          : env
            ? `Merged into ${base} — saved as ${version.label}, which this environment now tests`
            : `Merged into ${base} — saved as ${version.label}. Environments following production test it on their next run.`,
        { variant: releases ? "success" : "info" },
      );
    }
    postSlack({
      event: "pr",
      title: `Fix merged · ${pr.repo} ${ref}`,
      lines: [
        pr.title,
        pr.origin === "error_feed"
          ? "Error Feed will flag it if the issue comes back."
          : "Re-run the same scenarios to confirm it in simulation.",
      ],
      actions: [pr.origin === "error_feed" ? "Open issue" : "Re-run to verify"],
      links:
        pr.origin === "error_feed" && pr.clusterId
          ? { "Open issue": paths.dashboard.errorFeed.detail(pr.clusterId) }
          : pr.envId
            ? {
                "Re-run to verify": paths.dashboard.simulate.environmentStep(
                  pr.envId,
                  "runs",
                ),
              }
            : {},
    });
  };

  return (
    <Shell>
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <PrStatus status={pr.status} />
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2_1", fontWeight: 500 }}>
            {pr.title}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            <Box
              component="span"
              sx={{ fontFamily: MONO }}
            >{`${pr.repo} ${ref}`}</Box>
            {/* A coding agent's PR comes from its own branch, which we don't name. */}
            {pr.branch
              ? ` · ${pr.branch} → ${pr.base}`
              : pr.by
                ? ` · opened by ${pr.by}`
                : ""}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          onClick={() =>
            enqueueSnackbar(
              `In the product this opens the ${review.long} on ${hostLabel}.`,
              { variant: "info" },
            )
          }
          startIcon={<Iconify icon={hostIcon} width={14} />}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          {`View on ${hostLabel}`}
        </Button>
      </Stack>

      {/* The check we post on the PR — the evidence where the review happens. */}
      {!pr.check && !telemetry && !verify && pr.status === "open" && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            mt: 1.25,
            px: 1.25,
            py: 0.875,
            borderRadius: 1,
            bgcolor: (t) => alpha(t.palette.text.primary, 0.04),
          }}
        >
          <Iconify
            icon="solar:clock-circle-linear"
            width={16}
            sx={{ color: "text.subtitle" }}
          />
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            Future AGI simulation
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Queued — runs the matching scenarios against this branch
          </Typography>
        </Stack>
      )}
      {pr.check && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            mt: 1.25,
            px: 1.25,
            py: 0.875,
            borderRadius: 1,
            bgcolor: (t) => alpha(t.palette.text.primary, 0.04),
          }}
        >
          <Iconify
            icon="solar:check-circle-bold"
            width={16}
            sx={{ color: "status.success.fg" }}
          />
          <Typography sx={{ typography: "s3", fontWeight: 600 }}>
            Future AGI simulation
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`${prCheckSummary(pr.check) || "Passed"} on this branch`}
          </Typography>
        </Stack>
      )}
      {verify && <PrVerifyCheck pr={pr} now={now} />}

      {pr.status === "open" && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ mt: 1.25 }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}
          >
            {standing === "failed"
              ? `The Future AGI check failed on ${headOf(pr)} — a must-pass scenario broke since the ${review.short} opened. Push a fix to re-run it; merging is still your call.`
              : standing
                ? `The Future AGI check is running on ${headOf(pr)}.`
                : telemetry
                  ? "Waiting on review. Set FI_API_KEY and FI_SECRET_KEY where the agent runs before merging."
                  : "Waiting on review. Nothing changes in your agent until it's merged."}
          </Typography>
          <Button
            size="small"
            onClick={() => updatePr(pr.id, { status: "closed" })}
            sx={{ color: "text.secondary" }}
          >
            Close
          </Button>
          {/* The check is evidence, not a gate — merging is always the team's call. */}
          <Button
            size="small"
            variant="outlined"
            onClick={merge}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Mark as merged
          </Button>
        </Stack>
      )}
      {pr.status === "merged" && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ mt: 1.25 }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.secondary", flex: 1 }}
          >
            {telemetry
              ? "Merged — tracing is on. Traces show up in Observe once this deploys."
              : pr.mergedInto && pr.released === false
                ? `Merged into ${pr.mergedInto} — ${notReleasedWhy({ watch: pr.watch, ref: pr.watchRef })}${pr.version ? `. Saved as agent ${pr.version}` : ""}.`
                : pr.origin === "error_feed"
                  ? `${mergedLine(pr)}${pr.version ? ` — saved as agent ${pr.version}` : ""}. Error Feed will flag it if the issue comes back.`
                  : pr.version
                    ? `${mergedLine(pr)} — saved as agent ${pr.version}. Re-run the same scenarios to confirm the fix holds.`
                    : `${mergedLine(pr)}. Re-run the same scenarios to confirm the fix holds.`}
          </Typography>
          {onRerun && !telemetry && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={onRerun}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
            >
              Re-run to verify
            </Button>
          )}
        </Stack>
      )}
      {pr.status === "closed" && (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", mt: 1 }}
        >{`Closed without merging — ${telemetry ? "tracing wasn't added" : "the fix didn't ship"}. It stays here for the record.`}</Typography>
      )}
    </Shell>
  );
}

PrCard.propTypes = {
  prId: PropTypes.string,
  env: PropTypes.object,
  envState: PropTypes.object,
  onRerun: PropTypes.func,
  onMerged: PropTypes.func,
};

/* Why a merge didn't release: the base isn't the watched branch, or a tag is watched. */
function notReleasedWhy({ watch, ref }) {
  return watch === "tag"
    ? `released once tag ${ref || "prod"} points at it`
    : "not the release branch, not released";
}
/* "Merged into main — released", for a PR merged since the base was recorded. */
function mergedLine(pr) {
  return pr.mergedInto ? `Merged into ${pr.mergedInto} — released` : "Merged";
}

function Shell({ children }) {
  return (
    <Box
      sx={{
        p: 1.75,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
      }}
    >
      {children}
    </Box>
  );
}
Shell.propTypes = { children: PropTypes.node };
