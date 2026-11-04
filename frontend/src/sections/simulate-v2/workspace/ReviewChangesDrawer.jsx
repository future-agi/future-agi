import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import SideDrawer from "../components/SideDrawer";
import {
  EDIT_AREAS,
  areaOfEdit,
  diffOf,
  sourceLabelOf,
  sourceLinkOf,
  acceptRestPatch,
  decidePatch,
  decisionOf,
  rejectEditPatch,
  rejectableOf,
  restoreEditPatch,
  restorableOf,
  reviewCountsOf,
  withEditShown,
} from "../_mock/editReview";
import { focusChatAt } from "./reviewChangesBus";
import { castFor, getActor } from "../_mock/actors";
import { toolSettingOf } from "../_mock/toolEnv";

const MONO = "ui-monospace, Menlo, monospace";

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
      })
    : "";

/* A line of a change's diff: added, removed or changed — colour on the change only. */
const SIGN_COLOR = {
  "+": "status.success.fg",
  "−": "status.danger.fg",
  "~": "text.secondary",
};

/* One change's decision: Accept / Reject, then the outcome with a way back. */
function Decision({ decision, rejectable, restorable, onDecide }) {
  const quiet = { color: "text.primary", borderColor: "divider", minWidth: 0 };
  if (decision === "accepted")
    return (
      <Stack direction="row" alignItems="center" spacing={0.5} flexShrink={0}>
        <Iconify
          icon="solar:check-circle-linear"
          width={14}
          sx={{ color: "status.success.fg" }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Accepted
        </Typography>
        <Button
          variant="outlined"
          size="small"
          color="inherit"
          onClick={() => onDecide(null)}
          sx={{ minWidth: 0 }}
        >
          Change
        </Button>
      </Stack>
    );
  if (decision === "rejected")
    return (
      <Stack direction="row" alignItems="center" spacing={0.5} flexShrink={0}>
        <Iconify
          icon="solar:close-circle-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Rejected
        </Typography>
        <Tooltip arrow title={restorable.ok ? "" : restorable.why}>
          <span>
            <Button
              variant="outlined"
              size="small"
              color="inherit"
              disabled={!restorable.ok}
              onClick={() => onDecide(null)}
              sx={{ minWidth: 0 }}
            >
              Restore
            </Button>
          </span>
        </Tooltip>
      </Stack>
    );
  return (
    <Stack direction="row" spacing={0.75} flexShrink={0}>
      <Button
        size="small"
        variant="outlined"
        onClick={() => onDecide("accepted")}
        sx={quiet}
      >
        Accept
      </Button>
      <Tooltip arrow title={rejectable.ok ? "" : rejectable.why}>
        <span>
          <Button
            size="small"
            variant="outlined"
            disabled={!rejectable.ok}
            onClick={() => onDecide("rejected")}
            sx={quiet}
          >
            Reject
          </Button>
        </span>
      </Tooltip>
    </Stack>
  );
}
Decision.propTypes = {
  decision: PropTypes.oneOf(["accepted", "rejected", null]),
  rejectable: PropTypes.object.isRequired,
  restorable: PropTypes.object.isRequired,
  onDecide: PropTypes.func.isRequired,
};

/**
 * Review changes (roadmap builder.md Week 1; TH-8931): the unsaved edits to
 * this environment — from the builder chat, from attached files or by hand —
 * grouped by what they change. Each reads as before → after, names where it
 * came from (the chat message, the file, the tab), and is accepted or
 * rejected on its own (data.md §3) — a rejection can be restored until the
 * save. Save waits until every change is decided. Changes that couldn't
 * apply say why, with a fix. The scenarios the save rechecks are named, and
 * Save makes the accepted ones a named version.
 */
export default function ReviewChangesDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  label,
  cost,
  canEdit,
  canDiscard,
  onDiscard,
  onSave,
  thenRun = false,
}) {
  const navigate = useNavigate();
  /* Rows open to their diff; several can be open to compare. */
  const [openRows, setOpenRows] = useState({});
  const [note, setNote] = useState("");
  const [showNames, setShowNames] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const list = envState?.unsavedEnvChanges || [];
  const allFailed = envState?.failedEnvChanges || [];
  /* Dismissed failures stay recoverable: hidden behind "N dismissed". */
  const failed = allFailed.filter((f) => !f.dismissed);
  const dismissed = allFailed.filter((f) => f.dismissed);
  const setDismissed = (id, value) =>
    patch({
      failedEnvChanges: allFailed.map((x) =>
        x.id === id ? { ...x, dismissed: value || undefined } : x,
      ),
    });
  const counts = reviewCountsOf(list);
  if (!open) return <SideDrawer open={false} onClose={onClose} />;
  const groups = [
    ...EDIT_AREAS,
    { id: "other", label: "Other", tab: "settings" },
  ]
    .map((a) => ({
      ...a,
      items: list
        .map((c, i) => ({ c, i }))
        .filter(({ c }) => areaOfEdit(c).id === a.id),
    }))
    .filter((g) => g.items.length);

  const goTab = (tab, params = "") => {
    onClose();
    navigate(
      `${paths.dashboard.simulate.environmentStep(env.id, tab)}${params}`,
    );
  };
  const goSource = (link, tab, c) => {
    if (link.kind === "chat") {
      onClose();
      focusChatAt(c.at);
    } else if (link.kind === "file")
      goTab(
        "world",
        `?source=${encodeURIComponent(link.sourceId)}${link.at ? `&at=${encodeURIComponent(link.at)}` : ""}`,
      );
    else goTab(tab);
  };
  const names = cost?.scenarioNames || [];
  /* Ids read as names (an actor's id → its name); rules read against the
     base wording. */
  const diffOpts = {
    baseRules: env?.rules || [],
    nameOfId: (field, id) =>
      ["actors", "actorEdits", "customActors"].includes(field)
        ? getActor(id, envState)?.name
        : null,
    /* A tool never set before had its default tier — "Recorded → Simulated". */
    baseOf: (field, key) => {
      if (field === "actors" && key === undefined) return castFor(env);
      if (field !== "toolEnv") return null;
      const tool = (env?.tools || []).find((t) => t.name === key) || {
        name: key,
      };
      const { [key]: _set, ...rest } = envState?.toolEnv || {};
      return { tier: toolSettingOf(tool, { ...envState, toolEnv: rest }).tier };
    },
  };

  return (
    <SideDrawer open onClose={onClose} width={640}>
      <Stack sx={{ height: "100%", minHeight: 0 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 2,
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Review changes
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`${list.length - counts.rejected} unsaved edit${list.length - counts.rejected === 1 ? "" : "s"}${counts.rejected ? ` · ${counts.rejected} rejected` : ""} since environment ${label}. Accept or reject each one, then save. Runs use ${label} until they're saved.`}
            </Typography>
          </Box>
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify icon="mingcute:close-line" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 2, py: 2 }}>
          {!list.length && !failed.length ? (
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Nothing waiting. Every edit is saved.
            </Typography>
          ) : (
            <Stack spacing={2}>
              {/* Changes that couldn't apply: the draft keeps the rest. */}
              {failed.length > 0 && (
                <Box>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}
                  >
                    {`Couldn't apply · ${failed.length}`}
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                    }}
                  >
                    {failed.map((f) => (
                      <Stack
                        key={f.id}
                        direction="row"
                        spacing={1}
                        alignItems="flex-start"
                        sx={{
                          px: 1.5,
                          py: 1,
                          "& + &": {
                            borderTop: "1px solid",
                            borderColor: "divider",
                          },
                        }}
                      >
                        <Iconify
                          icon="solar:danger-triangle-linear"
                          width={16}
                          sx={{
                            color: "status.warning.fg",
                            flexShrink: 0,
                            mt: "2px",
                          }}
                        />
                        <Box flex={1} minWidth={0}>
                          <Typography sx={{ typography: "s2" }}>
                            {f.detail}
                          </Typography>
                          <Typography
                            sx={{ typography: "s3", color: "text.secondary" }}
                          >
                            {f.reason}
                          </Typography>
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {[
                              f.source && `From ${f.source}`,
                              f.scenarios?.length
                                ? `holds up ${f.scenarios.slice(0, 3).join(", ")}${f.scenarios.length > 3 ? ` and ${f.scenarios.length - 3} more` : ""}`
                                : "no scenario depends on it",
                              "the rest of the draft saves without it",
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </Typography>
                        </Box>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() =>
                            f.sourceId
                              ? goTab(
                                  "world",
                                  `?source=${encodeURIComponent(f.sourceId)}`,
                                )
                              : goTab("world")
                          }
                          sx={{
                            color: "text.primary",
                            borderColor: "divider",
                            flexShrink: 0,
                          }}
                        >
                          {f.source ? `Fix in ${f.source}` : "Fix"}
                        </Button>
                        {canEdit && (
                          <Button
                            size="small"
                            color="inherit"
                            onClick={() => setDismissed(f.id, true)}
                            sx={{ minWidth: 0, color: "text.secondary" }}
                          >
                            Dismiss
                          </Button>
                        )}
                      </Stack>
                    ))}
                  </Box>
                </Box>
              )}

              {dismissed.length > 0 && (
                <Box>
                  <Button
                    variant="outlined"
                    size="small"
                    color="inherit"
                    onClick={() => setShowDismissed((v) => !v)}
                    sx={{ minWidth: 0, px: 0 }}
                  >
                    {`${dismissed.length} dismissed · ${showDismissed ? "Hide" : "Show"}`}
                  </Button>
                  <Collapse in={showDismissed} unmountOnExit>
                    <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                      {dismissed.map((f) => (
                        <Stack
                          key={f.id}
                          direction="row"
                          alignItems="center"
                          spacing={1}
                        >
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              flex: 1,
                              minWidth: 0,
                            }}
                          >
                            {f.detail}
                          </Typography>
                          {canEdit && (
                            <Button
                              variant="outlined"
                              size="small"
                              color="inherit"
                              onClick={() => setDismissed(f.id, false)}
                              sx={{ minWidth: 0 }}
                            >
                              Bring back
                            </Button>
                          )}
                        </Stack>
                      ))}
                    </Stack>
                  </Collapse>
                </Box>
              )}

              {groups.map((g) => (
                <Box key={g.id}>
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}
                  >
                    {`${g.label} · ${g.items.length}`}
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                    }}
                  >
                    {g.items.map(({ c, i }) => {
                      const decision = decisionOf(list, i);
                      const rj = rejectableOf(list, i);
                      const rs = restorableOf(list, i);
                      const src = sourceLabelOf(c);
                      const link = sourceLinkOf(c, envState);
                      /* A rejected edit is already out of the world: read its diff as it was. */
                      const diff = diffOf(
                        c,
                        withEditShown(envState, c),
                        diffOpts,
                      );
                      const rowKey = `${c.at}-${i}`;
                      const expanded = !!openRows[rowKey];
                      return (
                        <Box
                          key={rowKey}
                          sx={{
                            "& + &": {
                              borderTop: "1px solid",
                              borderColor: "divider",
                            },
                          }}
                        >
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            sx={{ px: 1.5, minHeight: 44, py: 0.5 }}
                          >
                            <IconButton
                              size="small"
                              aria-label={
                                expanded ? "Hide the change" : "Show the change"
                              }
                              disabled={!diff.length}
                              onClick={() =>
                                setOpenRows((o) => ({
                                  ...o,
                                  [rowKey]: !o[rowKey],
                                }))
                              }
                              sx={{ flexShrink: 0, ml: -0.5 }}
                            >
                              <Iconify
                                icon={
                                  expanded
                                    ? "solar:alt-arrow-down-linear"
                                    : "solar:alt-arrow-right-linear"
                                }
                                width={14}
                                sx={{ color: "text.tertiary" }}
                              />
                            </IconButton>
                            <Box flex={1} minWidth={0}>
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s2",
                                  /* Rejected: still listed until the save, so it can come back. */
                                  ...(decision === "rejected" && {
                                    color: "text.tertiary",
                                    textDecoration: "line-through",
                                  }),
                                }}
                                title={c.detail}
                              >
                                {c.detail || g.label}
                              </Typography>
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s3",
                                  color: "text.tertiary",
                                }}
                              >
                                {[src, link.where, when(c.at)]
                                  .filter(Boolean)
                                  .join(" · ")}
                              </Typography>
                            </Box>
                            <Button
                              variant="outlined"
                              size="small"
                              color="inherit"
                              onClick={() => goSource(link, g.tab, c)}
                              sx={{
                                minWidth: 0,
                                flexShrink: 0,
                              }}
                            >
                              {link.label}
                            </Button>
                            {canEdit && (
                              <Decision
                                decision={decision}
                                rejectable={rj}
                                restorable={rs}
                                onDecide={(d) =>
                                  patch(
                                    d === "rejected"
                                      ? rejectEditPatch(envState, i)
                                      : decision === "rejected"
                                        ? restoreEditPatch(envState, i)
                                        : decidePatch(envState, i, d),
                                  )
                                }
                              />
                            )}
                          </Stack>
                          {/* Before → after, as the person would read it. */}
                          <Collapse in={expanded} unmountOnExit>
                            <Stack
                              spacing={0.25}
                              sx={{ px: 1.5, pb: 1.25, pl: 5.5 }}
                            >
                              {diff.slice(0, 12).map((d, n) => (
                                <Stack
                                  key={n}
                                  direction="row"
                                  spacing={1}
                                  alignItems="baseline"
                                >
                                  <Typography
                                    sx={{
                                      typography: "s3",
                                      fontFamily: MONO,
                                      fontWeight: 600,
                                      color: SIGN_COLOR[d.sign],
                                      width: 10,
                                      flexShrink: 0,
                                    }}
                                  >
                                    {d.sign}
                                  </Typography>
                                  <Typography
                                    sx={{
                                      typography: "s3",
                                      color: "text.tertiary",
                                      width: 92,
                                      flexShrink: 0,
                                    }}
                                  >
                                    {d.field}
                                  </Typography>
                                  <Box sx={{ minWidth: 0 }}>
                                    {/* Changed wording: the old struck through above the new. */}
                                    {d.before && (
                                      <Typography
                                        sx={{
                                          typography: "s3",
                                          fontFamily: MONO,
                                          color: "text.tertiary",
                                          textDecoration: "line-through",
                                          wordBreak: "break-word",
                                        }}
                                      >
                                        {d.before}
                                      </Typography>
                                    )}
                                    <Typography
                                      sx={{
                                        typography: "s3",
                                        fontFamily: MONO,
                                        color: "text.primary",
                                        wordBreak: "break-word",
                                      }}
                                    >
                                      {d.text}
                                    </Typography>
                                  </Box>
                                </Stack>
                              ))}
                              {diff.length > 12 && (
                                <Typography
                                  sx={{
                                    typography: "s3",
                                    color: "text.tertiary",
                                  }}
                                >
                                  {`and ${diff.length - 12} more`}
                                </Typography>
                              )}
                            </Stack>
                          </Collapse>
                        </Box>
                      );
                    })}
                  </Box>
                </Box>
              ))}

              {/* Changes to graded content: the scenarios the save rechecks, by name. */}
              {names.length > 0 && (
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    px: 1.5,
                    py: 1,
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Iconify
                      icon="solar:refresh-linear"
                      width={16}
                      sx={{ color: "text.secondary", flexShrink: 0 }}
                    />
                    <Typography sx={{ typography: "s2", flex: 1 }}>
                      {`These change what's graded, so ${names.length} scenario${names.length === 1 ? "" : "s"} get rechecked when you save`}
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      color="inherit"
                      onClick={() => setShowNames((v) => !v)}
                      sx={{ minWidth: 0 }}
                    >
                      {showNames ? "Hide" : "Show which"}
                    </Button>
                  </Stack>
                  <Collapse in={showNames} unmountOnExit>
                    <Stack component="ul" sx={{ m: 0, mt: 0.5, pl: 3.5 }}>
                      {names.slice(0, 20).map((n, k) => (
                        <Typography
                          key={k}
                          component="li"
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {n}
                        </Typography>
                      ))}
                      {names.length > 20 && (
                        <Typography
                          component="li"
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {`and ${names.length - 20} more`}
                        </Typography>
                      )}
                    </Stack>
                  </Collapse>
                </Box>
              )}
              {cost?.comparability && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {cost.comparability}
                </Typography>
              )}
            </Stack>
          )}
        </Box>

        {canEdit && list.length > 0 && (
          <Stack
            spacing={1}
            sx={{
              px: 2,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {/* Save as a named version: the note names it in History. */}
            <TextField
              size="small"
              fullWidth
              label="Name this version (optional)"
              placeholder="For example: Refund window from the 2026 policy"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            {/* Every change is decided before the save (data.md §3). */}
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                sx={{
                  typography: "s3",
                  color: counts.undecided ? "text.secondary" : "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {[
                  `${counts.total - counts.undecided} of ${counts.total} decided`,
                  counts.accepted && `${counts.accepted} accepted`,
                  counts.rejected &&
                    `${counts.rejected} rejected — taken out of the world, restore any until you save`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
              {counts.undecided > 0 && (
                <Button
                  variant="outlined"
                  size="small"
                  color="inherit"
                  onClick={() => patch(acceptRestPatch(envState))}
                  sx={{ minWidth: 0, py: 0 }}
                >
                  {counts.undecided === counts.total
                    ? "Accept all"
                    : "Accept all remaining"}
                </Button>
              )}
            </Stack>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Tooltip
                arrow
                title={
                  canDiscard
                    ? ""
                    : `Environment ${label} didn't keep its settings, so these can't be discarded in one step. Reject them one by one, or save them.`
                }
              >
                <span>
                  <Button
                    variant="outlined"
                    size="small"
                    color="inherit"
                    disabled={!canDiscard}
                    onClick={onDiscard}
                  >
                    Discard all
                  </Button>
                </span>
              </Tooltip>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
                noWrap
              >
                {cost?.text}
              </Typography>
              <Tooltip
                arrow
                title={
                  counts.undecided
                    ? `Accept or reject the ${counts.undecided} change${counts.undecided === 1 ? "" : "s"} still waiting first.`
                    : !counts.accepted
                      ? "Every change is rejected — Discard all puts the world back."
                      : ""
                }
              >
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={!!counts.undecided || !counts.accepted}
                    onClick={() => {
                      onSave(note);
                      setNote("");
                    }}
                  >
                    {`${cost?.label ? `Save environment ${cost.label}` : "Save"}${thenRun ? " and run" : ""}`}
                  </Button>
                </span>
              </Tooltip>
            </Stack>
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

ReviewChangesDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  label: PropTypes.string,
  cost: PropTypes.object,
  canEdit: PropTypes.bool,
  canDiscard: PropTypes.bool,
  onDiscard: PropTypes.func,
  onSave: PropTypes.func,
  /* A run waits on this save: Save says it starts the run too. */
  thenRun: PropTypes.bool,
};
