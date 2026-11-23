import PropTypes from "prop-types";
import { useEffect, useState } from "react";
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
  whoWhenOf,
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
import { currentUser } from "../_mock/scenarioProvenance";
import {
  VALIDATE_MS,
  finishValidating,
  startValidating,
  validatingLabel,
  validatingOf,
} from "../_mock/validating";

const MONO = "ui-monospace, Menlo, monospace";

/* A line of a change's diff: added, removed or changed — colour on the change only. */
const SIGN_COLOR = {
  "+": "status.success.fg",
  "−": "status.danger.fg",
  "~": "text.secondary",
};

/* One change's decision: two icon buttons; once decided, a word and a way back. */
function Decision({ decision, rejectable, restorable, onDecide }) {
  const icon = (title, glyph, onClick, disabled = false, color) => (
    <Tooltip arrow title={title}>
      <span>
        <IconButton
          size="small"
          aria-label={title}
          disabled={disabled}
          onClick={onClick}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "6px",
            p: 0.5,
            color: color || "text.secondary",
          }}
        >
          <Iconify icon={glyph} width={14} />
        </IconButton>
      </span>
    </Tooltip>
  );
  if (decision)
    return (
      <Stack direction="row" alignItems="center" spacing={0.75} flexShrink={0}>
        <Typography
          sx={{
            typography: "s3",
            color:
              decision === "accepted" ? "status.success.fg" : "text.tertiary",
          }}
        >
          {decision === "accepted" ? "Accepted" : "Rejected"}
        </Typography>
        {icon(
          decision === "accepted"
            ? "Undo"
            : restorable.ok
              ? "Restore"
              : restorable.why,
          "solar:undo-left-linear",
          () => onDecide(null),
          decision === "rejected" && !restorable.ok,
        )}
      </Stack>
    );
  return (
    <Stack direction="row" spacing={0.5} flexShrink={0}>
      {icon(
        "Accept",
        "solar:check-read-linear",
        () => onDecide("accepted"),
        false,
        "status.success.fg",
      )}
      {icon(
        rejectable.ok ? "Reject" : rejectable.why,
        "solar:close-circle-linear",
        () => onDecide("rejected"),
        !rejectable.ok,
      )}
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
  /* The version's name starts from the first accepted change (handover:
     "pre-fill an editable name"); typing replaces it. */
  const firstAccepted =
    list.find((c) => c.decision === "accepted") || list[0] || null;
  const suggestedName = String(firstAccepted?.detail || "")
    .replace(/\.$/, "")
    .slice(0, 80);
  const [nameTouched, setNameTouched] = useState(false);
  const shownName = nameTouched ? note : note || suggestedName;
  const names = cost?.scenarioNames || [];
  /* data.md §4 + backend: Save is refused until Validate has run on the
     affected scenarios — here, before the save, and again whenever the
     changes move. Validate lives on the environment's state, so it carries
     on when the drawer closes; the key it passed for is kept there too. */
  const checkKey = `${list.map((c) => `${c.at}${c.decision === "rejected" ? "-" : ""}`).join(",")}:${names.join("|")}`;
  const validating = validatingOf(envState);
  const reviewing = validating?.from === "review";
  const checkState =
    envState?.reviewValidated === checkKey
      ? "passed"
      : reviewing
        ? "running"
        : "idle";
  /* The scenario ids behind the names, matched the way the names were made. */
  const affectedIds = (envState?.scenarios || [])
    .filter((r) =>
      names.includes(
        [r.title || r.name || r.task || r.id, r.persona?.name]
          .filter(Boolean)
          .join(" · "),
      ),
    )
    .map((r) => r.id);
  const startedAt = reviewing ? validating.startedAt : null;
  useEffect(() => {
    if (!startedAt) return undefined;
    const left = Math.max(0, VALIDATE_MS - (Date.now() - Date.parse(startedAt)));
    const t = setTimeout(() => {
      finishValidating(patch);
      patch({ reviewValidated: checkKey });
    }, left);
    return () => clearTimeout(t);
  }, [startedAt, checkKey, patch]);
  const needsCheck = names.length > 0 && checkState !== "passed";
  /* Another Validate (re-proving scenarios) is already running. */
  const otherValidate = validating && !reviewing ? validating : null;
  const me = currentUser()?.name;
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
              {`${list.length - counts.rejected} unsaved edit${list.length - counts.rejected === 1 ? "" : "s"} since ${label} · runs use ${label} until you save`}
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
                        alignItems="center"
                        sx={{
                          px: 1.5,
                          minHeight: 40,
                          "& + &": {
                            borderTop: "1px solid",
                            borderColor: "divider",
                          },
                        }}
                      >
                        <Iconify
                          icon="solar:danger-triangle-linear"
                          width={16}
                          sx={{ color: "status.warning.fg", flexShrink: 0 }}
                        />
                        {/* One line; why, where from and what it holds up on hover. */}
                        <Tooltip
                          arrow
                          placement="bottom-start"
                          title={[
                            f.reason,
                            f.source && `From ${f.source}`,
                            f.scenarios?.length
                              ? `Holds up ${f.scenarios.slice(0, 3).join(", ")}${f.scenarios.length > 3 ? ` and ${f.scenarios.length - 3} more` : ""}`
                              : "No scenario depends on it — the rest saves without it",
                          ]
                            .filter(Boolean)
                            .join("\n")}
                          componentsProps={{
                            tooltip: { sx: { whiteSpace: "pre-line" } },
                          }}
                        >
                          <Typography
                            noWrap
                            sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                          >
                            {f.detail}
                          </Typography>
                        </Tooltip>
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
                          {/* What to do about it, not "fix the file" — a
                              source is never edited here (#97). */}
                          {/^missing table/i.test(f.detail || "")
                            ? "Supply the records"
                            : f.source
                              ? "Attach a file"
                              : "Fix"}
                        </Button>
                        {canEdit && (
                          <Tooltip arrow title="Dismiss">
                            <IconButton
                              size="small"
                              aria-label="Dismiss"
                              onClick={() => setDismissed(f.id, true)}
                              sx={{ color: "text.tertiary", p: 0.5 }}
                            >
                              <Iconify icon="mingcute:close-line" width={14} />
                            </IconButton>
                          </Tooltip>
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
                      const whoWhen = whoWhenOf(c, me);
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
                            sx={{ px: 1.5, minHeight: 40, py: 0.25 }}
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
                                {[src, whoWhen, link.where]
                                  .filter(Boolean)
                                  .join(" · ")}
                              </Typography>
                            </Box>
                            <Tooltip arrow title={link.label}>
                              <IconButton
                                size="small"
                                aria-label={link.label}
                                onClick={() => goSource(link, g.tab, c)}
                                sx={{
                                  color: "text.tertiary",
                                  p: 0.5,
                                  flexShrink: 0,
                                }}
                              >
                                <Iconify icon="solar:eye-linear" width={16} />
                              </IconButton>
                            </Tooltip>
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
                    <Tooltip arrow title={cost?.comparability || ""}>
                      <Typography sx={{ typography: "s2", flex: 1 }}>
                        {checkState === "passed"
                          ? `${names.length} affected scenario${names.length === 1 ? "" : "s"} validated — all pass`
                          : checkState === "running"
                            ? `${validatingLabel(validating)}…`
                            : `Validate the ${names.length} affected scenario${names.length === 1 ? "" : "s"} before you save`}
                      </Typography>
                    </Tooltip>
                    {checkState !== "passed" && (
                      <Tooltip
                        arrow
                        title={
                          otherValidate
                            ? `${validatingLabel(otherValidate)} already — this one can start once it finishes.`
                            : ""
                        }
                      >
                        <span>
                          <Button
                            variant="outlined"
                            size="small"
                            color="inherit"
                            disabled={
                              checkState === "running" ||
                              !!otherValidate ||
                              !canEdit
                            }
                            onClick={() =>
                              startValidating(
                                patch,
                                affectedIds.length
                                  ? affectedIds
                                  : names.map((_, k) => `affected-${k}`),
                                "review",
                              )
                            }
                            sx={{ minWidth: 0 }}
                          >
                            Validate
                          </Button>
                        </span>
                      </Tooltip>
                    )}
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
              label="Version name"
              placeholder="Name this version"
              value={shownName}
              onChange={(e) => {
                setNameTouched(true);
                setNote(e.target.value);
              }}
              inputProps={{ maxLength: 80 }}
              InputProps={{ sx: { typography: "s1" } }}
            />
            {/* What Save does, plainly (handover). */}
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`Save makes ${cost?.label ? `environment ${cost.label}` : "the next version"} from the accepted changes. ${thenRun ? "Then the run you asked for starts." : "It doesn't start a run."} Earlier runs keep the version they ran on.`}
            </Typography>
            {/* Every change is decided before the save (data.md §3). */}
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: counts.undecided ? "text.secondary" : "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {[
                  `${counts.total - counts.undecided} of ${counts.total} decided`,
                  counts.accepted && `${counts.accepted} accepted`,
                  counts.rejected && `${counts.rejected} rejected`,
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
                  sx={{ minWidth: 0 }}
                >
                  {counts.undecided === counts.total
                    ? "Accept all"
                    : "Accept the rest"}
                </Button>
              )}
              <Tooltip
                arrow
                title={
                  canDiscard
                    ? ""
                    : `Environment ${label} didn't keep its settings. Reject them one by one, or save them.`
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
              <Tooltip
                arrow
                title={
                  counts.undecided
                    ? `Accept or reject the ${counts.undecided} change${counts.undecided === 1 ? "" : "s"} still waiting first.`
                    : !counts.accepted
                      ? "Every change is rejected — Discard all puts the world back."
                      : needsCheck
                        ? checkState === "running"
                          ? "Saves once Validate finishes."
                          : "Validate the affected scenarios first."
                        : cost?.text || ""
                }
              >
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={
                      !!counts.undecided || !counts.accepted || needsCheck
                    }
                    onClick={() => {
                      onSave(shownName.trim());
                      setNote("");
                      setNameTouched(false);
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
