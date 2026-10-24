import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  currentEnvVersion,
  currentAgentVersion,
  pendingEnvChanges,
  saveEnvChangesPatch,
  envChangeLabel,
  isEnvDraft,
  scenariosInSet,
} from "../_mock/versions";
import { envLabelOf, pendingCostOf } from "../_mock/envVersionKinds";
import { envVersionNameOf } from "../_mock/codeRefs";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import useEnvViewAs from "./useEnvViewAs";
import VersionHistoryMenu from "./VersionHistory";

/**
 * What is being tested, against what — a run is always agent version ×
 * environment version, said the same way on every screen:
 *
 *   Agent v2 × env v7 (68)   [Drifted · 2]
 *
 * The environment version is one snapshot of the world, scenario set,
 * sub-goals, evals and simulated caller (envSnapshot.js); the parts behind it
 * are in the tooltip and the version history. An agent change moves only the
 * agent; the environment moves on an edit or an approved nudge. While a nudge
 * is open the environment reads Drifted, and the chip opens the inbox.
 *
 * An edit says what it will cost before it lands (AC-23.3): which version
 * moves and what that does to comparability. While someone views an older
 * version (?viewAs=), a slim read-only banner sits under the bar.
 */
export default function VersionBar({ env, envState, patch, locked = false }) {
  const { viewAs, clearViewAs } = useEnvViewAs(env, envState);
  const envV = currentEnvVersion(env, envState);
  /* Viewing an older version names that version and its own suite. */
  const scenarioCount = scenariosInSet(envState, viewAs || envV.label).length;
  const agentV = currentAgentVersion(envState);
  /* "main @ 3f9a2c1" for a code agent, "v2" for any other. */
  const agentName = agentV
    ? envVersionNameOf(envState, agentV.label, env?.id).main
    : null;
  const pending = pendingEnvChanges(envState);
  const snap = viewAs ? null : currentEnvSnapshot(env, envState);
  const cost = pendingCostOf(env, envState);
  const shownLabel = viewAs
    ? envLabelOf(env, envState, viewAs)
    : snap?.label || envLabelOf(env, envState, envV.label);
  /* Before the first run nothing is a version yet: edits land in place. */
  const draft = !viewAs && isEnvDraft(envState);
  const [historyAnchor, setHistoryAnchor] = useState(null);
  const strong = (t) => (
    <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
      {t}
    </Box>
  );

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          flexWrap: "wrap",
          rowGap: 1,
        }}
      >
        <Tooltip
          arrow
          title={
            <Box>
              <Typography
                sx={{ typography: "s3", color: "common.white", mb: 0.5 }}
              >
                An agent change moves only the agent. The environment version
                moves when the world, scenarios, sub-goals, evaluations or
                simulated caller change.
              </Typography>
              <Typography sx={{ typography: "s3", color: "common.white" }}>
                <b>{agentV ? `Agent ${agentName}` : "No agent"}</b>
                {" — the test subject"}
              </Typography>
              {draft ? (
                <Typography sx={{ typography: "s3", color: "common.white" }}>
                  <b>{`Environment ${shownLabel}`}</b> — not run yet. Edits land
                  in place until its first run fixes it.
                </Typography>
              ) : (
                <Typography sx={{ typography: "s3", color: "common.white" }}>
                  <b>{`Environment ${shownLabel}`}</b>
                  {snap?.summary ? ` — ${snap.summary}` : ""}
                </Typography>
              )}
              {snap?.parts?.caller && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "common.white",
                    opacity: 0.8,
                    mt: 0.25,
                  }}
                >{`Simulated caller: ${snap.parts.caller}`}</Typography>
              )}
            </Box>
          }
        >
          <Typography
            noWrap
            sx={{
              typography: "s2",
              color: "text.secondary",
              cursor: "default",
              fontVariantNumeric: "tabular-nums",
              minWidth: 0,
            }}
          >
            <Iconify
              icon="solar:box-linear"
              width={14}
              sx={{ color: "text.tertiary", verticalAlign: "-2px", mr: 0.75 }}
            />
            {agentV ? <>Agent {strong(agentName)}</> : "No agent yet"}
            <Typography
              component="span"
              sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}
            >
              ×
            </Typography>
            environment {strong(shownLabel)}
            {draft && (
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {" "}
                · not run yet
              </Box>
            )}
            <Box
              component="span"
              sx={{ typography: "s3", color: "text.tertiary" }}
            >{` · ${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`}</Box>
          </Typography>
        </Tooltip>

        {/* Drift is said once, by the chip in the environment header. */}
        <Box flex={1} />

        {pending.length > 0 && !viewAs && (
          <Tooltip
            arrow
            title={`${pending.map((c) => c.detail || envChangeLabel(c.kind)).join(" · ")}. ${cost?.comparability || ""} Runs use environment ${shownLabel} until these are saved.`}
          >
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  cursor: "default",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${pending.length} edit${pending.length === 1 ? "" : "s"} since environment ${shownLabel} · `}
                <Box
                  component="span"
                  sx={{ color: "text.secondary", fontWeight: 600 }}
                >
                  {cost?.text}
                </Box>
              </Typography>
              {!locked && patch && !viewAs && (
                <Button
                  size="small"
                  onClick={() => patch(saveEnvChangesPatch(env, envState))}
                  sx={{ minWidth: 0 }}
                >
                  {`Save as environment ${cost?.label}`}
                </Button>
              )}
            </Stack>
          </Tooltip>
        )}

        <Button
          size="small"
          onClick={(e) => setHistoryAnchor(e.currentTarget)}
          startIcon={<Iconify icon="solar:history-linear" width={14} />}
          sx={{ color: "text.secondary", minWidth: 0 }}
        >
          History
        </Button>
        <VersionHistoryMenu
          anchor={historyAnchor}
          onClose={() => setHistoryAnchor(null)}
          env={env}
          envState={envState}
          patch={patch}
          readOnly={locked}
        />
      </Stack>

      {/* Viewing another version — read-only, held in the URL, one way back. */}
      {viewAs && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:eye-linear"
            width={14}
            sx={{ color: "text.tertiary" }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Viewing as{" "}
            <Box
              component="span"
              sx={{ color: "text.primary", fontWeight: 600 }}
            >{`environment ${envLabelOf(env, envState, viewAs)}`}</Box>{" "}
            — read-only.
          </Typography>
          <Button size="small" onClick={clearViewAs} sx={{ minWidth: 0 }}>
            Back to current
          </Button>
        </Stack>
      )}
    </>
  );
}

VersionBar.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};
