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
  undoEditPatch,
  undoableOf,
} from "../_mock/editReview";
import { focusChatAt } from "./reviewChangesBus";
import { getActor } from "../_mock/actors";

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

/**
 * Review changes (roadmap builder.md Week 1; TH-8931): the unsaved edits to
 * this environment — from the builder chat, from attached files or by hand —
 * grouped by what they change. Each reads as before → after, names where it
 * came from (the chat message, the file, the tab), and can be kept or undone
 * on its own. Changes that couldn't apply say why, with a fix. The scenarios
 * the save rechecks are named, and Save makes them a named version.
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
}) {
  const navigate = useNavigate();
  /* Rows open to their diff; several can be open to compare. */
  const [openRows, setOpenRows] = useState({});
  const [note, setNote] = useState("");
  const [showNames, setShowNames] = useState(false);
  const list = envState?.unsavedEnvChanges || [];
  const failed = envState?.failedEnvChanges || [];
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
              {`${list.length} unsaved edit${list.length === 1 ? "" : "s"} since environment ${label}. Keep or undo each one, then save. Runs use ${label} until they're saved.`}
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
                            onClick={() =>
                              patch({
                                failedEnvChanges: failed.filter(
                                  (x) => x.id !== f.id,
                                ),
                              })
                            }
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
                      const u = undoableOf(list, i);
                      const src = sourceLabelOf(c);
                      const link = sourceLinkOf(c, envState);
                      const diff = diffOf(c, envState, diffOpts);
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
                                sx={{ typography: "s2" }}
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
                              size="small"
                              color="inherit"
                              onClick={() => goSource(link, g.tab, c)}
                              sx={{
                                minWidth: 0,
                                color: "text.secondary",
                                flexShrink: 0,
                              }}
                            >
                              {link.label}
                            </Button>
                            {canEdit && (
                              <Tooltip arrow title={u.ok ? "" : u.why}>
                                <span>
                                  <Button
                                    size="small"
                                    color="inherit"
                                    disabled={!u.ok}
                                    startIcon={
                                      <Iconify
                                        icon="solar:undo-left-round-linear"
                                        width={14}
                                      />
                                    }
                                    onClick={() =>
                                      patch(undoEditPatch(envState, i))
                                    }
                                    sx={{ minWidth: 0 }}
                                  >
                                    Undo
                                  </Button>
                                </span>
                              </Tooltip>
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
                      size="small"
                      color="inherit"
                      onClick={() => setShowNames((v) => !v)}
                      sx={{ minWidth: 0, color: "text.secondary" }}
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
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Tooltip
                arrow
                title={
                  canDiscard
                    ? ""
                    : `Environment ${label} didn't keep its settings, so these can't be discarded in one step. Undo them one by one, or save them.`
                }
              >
                <span>
                  <Button
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
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => {
                  onSave(note);
                  setNote("");
                }}
              >
                {`Save release ${cost?.label}`}
              </Button>
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
};
