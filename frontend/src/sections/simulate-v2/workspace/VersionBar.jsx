import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  Tooltip,
  Popover,
} from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import Iconify from "src/components/iconify";
import {
  currentEnvVersion,
  currentAgentVersion,
  pendingEnvChanges,
  saveEnvChangesPatch,
  discardEnvChangesPatch,
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
export default function VersionBar({
  env,
  envState,
  patch,
  locked = false,
  /* Opens the environment's History tab; without it, History is a menu. */
  onOpenHistory,
  /* Saves the edits — the workspace checks for a teammate's save first. */
  onSave,
}) {
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
  /* The unsaved edits, listed; Discard asks first. */
  const [editsAnchor, setEditsAnchor] = useState(null);
  const [discarding, setDiscarding] = useState(false);
  const discardPatch = pending.length
    ? discardEnvChangesPatch(env, envState)
    : null;
  const canEdit = !locked && !!patch && !viewAs;
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
          classes={{ tooltip: "ds-hovercard" }}
          arrow
          title={
            <Box>
              <Typography
                sx={{ typography: "s3", color: "text.primary", mb: 0.5 }}
              >
                An agent change moves only the agent. The environment version
                moves when the world, scenarios, sub-goals, evaluations or
                simulated caller change.
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.primary" }}>
                <b>{agentV ? `Agent ${agentName}` : "No agent"}</b>
                {" — the test subject"}
              </Typography>
              {draft ? (
                <Typography sx={{ typography: "s3", color: "text.primary" }}>
                  <b>{`Environment ${shownLabel}`}</b> — not run yet. Edits land
                  in place until its first run fixes it.
                </Typography>
              ) : (
                <Typography sx={{ typography: "s3", color: "text.primary" }}>
                  <b>{`Environment ${shownLabel}`}</b>
                  {snap?.summary ? ` — ${snap.summary}` : ""}
                </Typography>
              )}
              {snap?.parts?.caller && (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.primary",
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
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Button
              size="small"
              onClick={(e) => setEditsAnchor(e.currentTarget)}
              endIcon={
                <Iconify icon="solar:alt-arrow-down-linear" width={14} />
              }
              sx={{
                minWidth: 0,
                color: "text.secondary",
                typography: "s3",
                fontWeight: 400,
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
              }}
            >
              {`${pending.length} unsaved edit${pending.length === 1 ? "" : "s"} · `}
              <Box
                component="span"
                sx={{ color: "text.secondary", fontWeight: 600, ml: 0.5 }}
              >
                {cost?.text}
              </Box>
            </Button>
            {canEdit && (
              <Button
                size="small"
                variant="outlined"
                onClick={() =>
                  onSave ? onSave() : patch(saveEnvChangesPatch(env, envState))
                }
                sx={{
                  minWidth: 0,
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                  color: "text.primary",
                  borderColor: "divider",
                }}
              >
                {`Save as environment ${cost?.label}`}
              </Button>
            )}
          </Stack>
        )}
        <Popover
          open={!!editsAnchor}
          anchorEl={editsAnchor}
          onClose={() => setEditsAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { width: 400, mt: 0.5 } } }}
        >
          <Box
            sx={{
              px: 2,
              py: 1.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              {`Unsaved edits since environment ${shownLabel}`}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {`Runs use environment ${shownLabel} until these are saved. ${cost?.comparability || ""}`}
            </Typography>
          </Box>
          <Box sx={{ maxHeight: 280, overflowY: "auto" }}>
            {pending.map((c, i) => (
              <Stack
                key={`${c.kind}-${c.at || i}`}
                direction="row"
                spacing={1.5}
                sx={{
                  px: 2,
                  py: 1,
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    width: 112,
                    flexShrink: 0,
                  }}
                >
                  {envChangeLabel(c.kind)}
                </Typography>
                <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
                  {c.detail || envChangeLabel(c.kind)}
                </Typography>
              </Stack>
            ))}
          </Box>
          {canEdit && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 2,
                py: 1.25,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <Tooltip
                arrow
                title={
                  discardPatch
                    ? ""
                    : `Environment ${shownLabel} didn't keep its settings, so these can't be undone in one step — save them, or change them back by hand.`
                }
              >
                <span>
                  <Button
                    size="small"
                    disabled={!discardPatch}
                    onClick={() => {
                      setEditsAnchor(null);
                      setDiscarding(true);
                    }}
                    sx={{ color: "text.secondary" }}
                  >
                    Discard edits
                  </Button>
                </span>
              </Tooltip>
              <Box flex={1} />
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => {
                  setEditsAnchor(null);
                  onSave ? onSave() : patch(saveEnvChangesPatch(env, envState));
                }}
              >
                {`Save as environment ${cost?.label}`}
              </Button>
            </Stack>
          )}
        </Popover>
        <ConfirmDialog
          open={discarding}
          onClose={() => setDiscarding(false)}
          title={`Discard ${pending.length} edit${pending.length === 1 ? "" : "s"}?`}
          content={`The world goes back to environment ${shownLabel} as it was saved. This can't be undone.`}
          action={
            <Button
              size="small"
              variant="contained"
              color="error"
              onClick={() => {
                setDiscarding(false);
                if (discardPatch) patch(discardPatch);
              }}
            >
              Discard
            </Button>
          }
        />

        <Button
          size="small"
          onClick={(e) =>
            onOpenHistory ? onOpenHistory() : setHistoryAnchor(e.currentTarget)
          }
          variant="outlined"
          startIcon={<Iconify icon="solar:history-linear" width={16} />}
          sx={{
            minWidth: 0,
            flexShrink: 0,
            color: "text.primary",
            borderColor: "divider",
          }}
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
  onOpenHistory: PropTypes.func,
  onSave: PropTypes.func,
};
