import PropTypes from "prop-types";
import { createElement, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useEnvState } from "../../store";
import { useRoleGates } from "../../_mock/previewRole";
import {
  dataProposalOf,
  draftDataPatches,
  readFile,
  sourceEditPatch,
  sourceFromOrg,
} from "../../_mock/orgData";
import {
  personalChoicePatch,
  supplyRecordsPatch,
} from "../../_mock/dataMapping";
import { openReviewChanges } from "../reviewChangesBus";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";
import { currentEnvVersion, editsLandInPlace } from "../../_mock/versions";
import { personaFor } from "../../_mock/scenarios";
import { stampNewBatch } from "../../_mock/addScenarios";
import { asProposal } from "../../_mock/scenarioSources";
import { neutralCheckboxSx } from "../../components/primitives";
import { castOf } from "../../_mock/actors";
import AddSourceMenu from "./AddSourceMenu";
import PersonalDataChoice from "./PersonalDataChoice";

const KIND = {
  rule: { icon: "solar:document-text-linear", label: "Rule" },
  table: { icon: "solar:database-linear", label: "Seed data" },
  scenario: { icon: "solar:layers-minimalistic-linear", label: "Scenarios" },
  missing: { icon: "solar:danger-triangle-linear", label: "Missing" },
  conflict: { icon: "solar:danger-triangle-linear", label: "Conflict" },
  hidden: { icon: "solar:eye-closed-linear", label: "Hidden" },
  unreadable: { icon: "solar:danger-triangle-linear", label: "Can't read" },
  actor: { icon: "solar:users-group-rounded-linear", label: "Actor" },
  tool: { icon: "solar:widget-4-linear", label: "Tool" },
  state: { icon: "solar:database-linear", label: "Starting state" },
  reference: { icon: "solar:book-linear", label: "Reference" },
  personal: { icon: "solar:shield-user-linear", label: "Personal data" },
};

/* Grouped by what each change touches — the same areas as Review changes. */
const GROUPS = [
  { label: "Rules", kinds: ["conflict", "rule"] },
  { label: "Starting state and records", kinds: ["table", "state", "missing"] },
  { label: "Tools", kinds: ["tool"] },
  { label: "Actors", kinds: ["actor"] },
  { label: "Scenarios", kinds: ["scenario"] },
  { label: "Reference material", kinds: ["reference"] },
  { label: "Personal data — your choice", kinds: ["personal"] },
  { label: "Who sees what, and problems", kinds: ["hidden", "unreadable"] },
];

/**
 * What the builder proposes from the attached files (roadmap data.md §1
 * "Review changes"): each rule, table and case beside the passage or row it
 * came from, inferences labelled, conflicts and missing data as choices that
 * block only what depends on them. Accept selected saves one environment
 * version — the data snapshot runs will pin.
 */
export default function DataProposalCard({
  env,
  sourceIds,
  onOpenSource,
  onSaved,
  /* "drawer": fills a side drawer — the list scrolls, the footer stays. */
  layout = "card",
}) {
  const drawer = layout === "drawer";
  const { envState, patch } = useEnvState(env.id);
  const latest = useRef(envState);
  latest.current = envState;
  const gates = useRoleGates();
  const sources = useMemo(
    () => (envState.sources || []).filter((s) => sourceIds.includes(s.id)),
    [envState.sources, sourceIds],
  );
  const cast = useMemo(() => castOf(env, envState), [env, envState]);
  const proposal = useMemo(
    () =>
      dataProposalOf(sources, {
        cast,
        tools: (env.tools || []).map((t) => t.name),
        scenarios: envState.scenarios || [],
      }),
    [sources, cast, env.tools, envState.scenarios],
  );
  /* An item's text changed before accepting: a rule's wording, an approval scope. */
  const [edits, setEdits] = useState({});
  const [editing, setEditing] = useState(null);
  const editValueOf = (i) =>
    edits[i.id] ??
    (i.kind === "rule" ? i.rule : i.actor?.[i.editable?.field] || "");
  const [picked, setPicked] = useState(() =>
    proposal.items
      .filter((i) => !i.blocking && !["hidden", "personal"].includes(i.kind))
      .map((i) => i.id),
  );
  const [conflictPick, setConflictPick] = useState(null);
  const [saved, setSaved] = useState(null);
  const [rejected, setRejected] = useState(false);
  const next = nextSnapshotLabel(env, envState);
  /* No run has used the current version yet: accepted changes land on it
     now, so there's no next version to save (versions.js envChangePatch). */
  const inPlace = editsLandInPlace(envState);
  const current =
    currentEnvVersion(env, envState)?.label || "the current version";
  const pickable = (i) =>
    !i.blocking && !["hidden", "unreadable", "personal"].includes(i.kind);
  /* Each source with personal data needs a choice before anything is accepted. */
  const undecided = proposal.items.filter((i) => i.needsChoice);
  const hasChoices = proposal.items.some((i) => i.kind === "personal");
  const [supply, setSupply] = useState(null);
  const count =
    proposal.items.filter((i) => pickable(i) && picked.includes(i.id)).length +
    (conflictPick ? 1 : 0);

  const scenarioRows = (items) => {
    const rows = items.flatMap((i, n) =>
      i.scenario.map((s, k) => ({
        id: `${env.id}-src-${i.id}-${k}-${Date.now().toString(36)}`,
        title: s.t,
        task: s.k,
        situation: s.k,
        expected: s.e,
        outcome: s.e,
        persona: personaFor(env, n + k + 1),
        turns: 6,
        origin: i.cite.ref.split(" · ")[0],
        citedSource: i.cite.sourceId,
        citation: { ref: i.cite.ref, excerpt: i.cite.excerpt },
      })),
    );
    const { rows: stamped } = stampNewBatch(rows, {
      env,
      envState,
      source: "dataset-import",
    });
    return stamped.map((r) => asProposal(r, "dataset-import"));
  };

  const accept = () => {
    if (!gates.canEdit)
      return enqueueSnackbar(gates.editReason, { variant: "info" });
    /* data.md §3–4: accepted changes stay in the draft, and the version is
       saved from Review changes once the affected scenario checks pass. */
    const { patches, failed } = draftDataPatches(
      env,
      envState,
      proposal,
      picked,
      { conflictPick, scenarioRows, edits, cast },
    );
    patches.forEach((x) => patch(x));
    if (failed.length)
      patch({
        failedEnvChanges: [...(envState.failedEnvChanges || []), ...failed],
      });
    const n = patches.length - 1;
    if (inPlace) {
      enqueueSnackbar(
        `Added ${n} change${n === 1 ? "" : "s"} to environment ${current}${failed.length ? ` · ${failed.length} couldn't apply` : ""}. No run has used ${current} yet, so they apply now.`,
        { variant: "success" },
      );
      if (onSaved) onSaved();
      else setSaved({ label: current, rules: 0, inPlace: true });
      return null;
    }
    enqueueSnackbar(
      `Added ${n} change${n === 1 ? "" : "s"} to your unsaved changes${failed.length ? ` · ${failed.length} couldn't apply` : ""}. Review and save them to make the version.`,
      {
        variant: "success",
        action: (key) =>
          createElement(
            Button,
            {
              size: "small",
              color: "inherit",
              onClick: () => {
                closeSnackbar(key);
                openReviewChanges();
              },
            },
            "Review changes",
          ),
      },
    );
    if (onSaved) onSaved();
    else setSaved({ label: next, rules: 0, draft: true });
    return null;
  };
  const omitRows = (item) => {
    const joinsOf = (list) =>
      list.find((s) => s.id === item.sourceId)?.joins || {};
    patch(
      sourceEditPatch(envState, item.sourceId, {
        joins: { ...joinsOf(sources), [item.field]: "omit" },
      }),
    );
    enqueueSnackbar("Left those rows out.", {
      variant: "info",
      action: (key) =>
        createElement(
          Button,
          {
            size: "small",
            color: "inherit",
            onClick: () => {
              closeSnackbar(key);
              const now = latest.current;
              patch(
                sourceEditPatch(now, item.sourceId, {
                  joins: {
                    ...joinsOf(now.sources || []),
                    [item.field]: null,
                  },
                }),
              );
            },
          },
          "Undo",
        ),
    });
  };
  const supplyFiles = (files) => {
    const read = files.map(readFile);
    const usable = read.filter((r) => r.status !== "unsupported");
    if (usable.length)
      patch(supplyRecordsPatch(envState, supply.sourceId, supply.join, usable));
    enqueueSnackbar(
      usable.length
        ? `Added ${usable.map((r) => r.name).join(", ")} to the draft. Nothing reaches the world until you accept.`
        : read[0]?.statusNote || "That file can't be read.",
      { variant: usable.length ? "success" : "warning" },
    );
  };
  const supplyOrg = (o) => {
    if (!o.access) {
      enqueueSnackbar(
        `You don't have access to ${o.name}. Asked the ${o.owner} for it — you'll get a notification.`,
        { variant: "info" },
      );
      return;
    }
    patch(
      supplyRecordsPatch(envState, supply.sourceId, supply.join, [
        sourceFromOrg(o),
      ]),
    );
    enqueueSnackbar(
      `Copied ${o.name} rev ${o.revision} into the draft. Nothing reaches the world until you accept.`,
      { variant: "success" },
    );
  };

  const renderItem = (i) => {
    const k = KIND[i.kind];
    const on = picked.includes(i.id);
    return (
      <Stack
        key={i.id}
        direction="row"
        spacing={1}
        sx={{
          px: drawer ? 2.25 : 1,
          py: 1,
          "& + &": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        <Checkbox
          size="small"
          checked={pickable(i) && on}
          disabled={!pickable(i)}
          onChange={() =>
            setPicked((cur) =>
              on ? cur.filter((x) => x !== i.id) : [...cur, i.id],
            )
          }
          sx={{
            ...neutralCheckboxSx,
            p: 0.25,
            alignSelf: "flex-start",
            ...(i.kind === "personal" && { visibility: "hidden" }),
          }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Iconify
              icon={k.icon}
              width={14}
              sx={{
                color: i.blocking ? "status.warning.fg" : "text.tertiary",
                flexShrink: 0,
              }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {i.kind === "rule" && edits[i.id] != null
                ? `Rule: ${edits[i.id]}`
                : i.title}
            </Typography>
            {(i.kind === "rule" || i.editable) && pickable(i) && (
              <Tooltip
                arrow
                describeChild
                title={
                  i.kind === "rule"
                    ? "Changes the rule we'll add. The file stays as it is."
                    : "Changes what we'll add. The file stays as it is."
                }
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setEditing(editing === i.id ? null : i.id)}
                  sx={{ minWidth: 0, py: 0, px: 0.75, typography: "s3" }}
                >
                  {editing === i.id
                    ? "Done"
                    : i.kind === "rule"
                      ? "Edit rule"
                      : "Edit"}
                </Button>
              </Tooltip>
            )}
            {i.inferred && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                · inferred
              </Typography>
            )}
          </Stack>
          {i.detail &&
            (i.blocking ||
              ["missing", "conflict", "unreadable"].includes(i.kind)) &&
            !(i.editable && edits[i.id] != null) && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
              >
                {i.detail}
              </Typography>
            )}
          {i.kind === "missing" && i.present !== false && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
            >
              {i.affects?.length
                ? `${i.affects.length} ${i.affects.length === 1 ? "scenario uses" : "scenarios use"} these rows`
                : "Kept, flagged until resolved"}
            </Typography>
          )}
          {i.editable && edits[i.id] != null && editing !== i.id && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
            >
              {`${i.editable.label}: ${edits[i.id]} · edited`}
            </Typography>
          )}
          {editing === i.id && (
            <TextField
              size="small"
              fullWidth
              autoFocus
              label={i.kind === "rule" ? "Rule" : i.editable.label}
              value={editValueOf(i)}
              onChange={(e) =>
                setEdits((cur) => ({ ...cur, [i.id]: e.target.value }))
              }
              onKeyDown={(e) => e.key === "Enter" && setEditing(null)}
              sx={{ mt: 0.75 }}
            />
          )}
          {/* data.md §3: values the builder made up say so — never
              attributed to an uploaded source. */}
          {!i.cite && !["conflict", "unreadable"].includes(i.kind) && (
            <Typography
              sx={{ mt: 0.5, typography: "s3", color: "text.tertiary" }}
            >
              Generated, not from a file
            </Typography>
          )}
          {i.kind === "table" &&
            proposal.items.find(
              (x) => x.kind === "personal" && x.sourceId === i.cite?.sourceId,
            )?.choice === "replace" && (
              <Typography
                sx={{ mt: 0.5, typography: "s3", color: "text.tertiary" }}
              >
                Personal fields replaced
              </Typography>
            )}
          {i.cite && (
            <Tooltip
              title={i.cite.excerpt ? `“${i.cite.excerpt}”` : ""}
              arrow
              placement="bottom-start"
            >
              <ButtonBase
                onClick={() => onOpenSource?.(i.cite.sourceId)}
                sx={{
                  mt: 0.5,
                  typography: "s3",
                  color: "text.tertiary",
                  textAlign: "left",
                  display: "block",
                  "&:hover": { color: "text.primary" },
                }}
              >
                <Iconify
                  icon="solar:link-minimalistic-linear"
                  width={12}
                  sx={{ mr: 0.5, verticalAlign: "-2px" }}
                />
                {i.cite.ref}
              </ButtonBase>
            </Tooltip>
          )}
          {i.kind === "conflict" && (
            <Stack spacing={0.5} sx={{ mt: 0.75 }} role="radiogroup">
              {i.options.map((o) => (
                <ButtonBase
                  key={o.sourceId}
                  role="radio"
                  aria-checked={conflictPick === o.sourceId}
                  onClick={() => setConflictPick(o.sourceId)}
                  sx={{
                    justifyContent: "flex-start",
                    gap: 0.75,
                    typography: "s3",
                    textAlign: "left",
                  }}
                >
                  <Iconify
                    icon={
                      conflictPick === o.sourceId
                        ? "solar:check-circle-bold"
                        : "solar:circle-linear"
                    }
                    width={14}
                  />
                  {`${o.label} applies: ${o.rule}`}
                </ButtonBase>
              ))}
            </Stack>
          )}
          {i.kind === "missing" && (
            <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
              <Button
                size="small"
                variant="outlined"
                onClick={(e) =>
                  setSupply({
                    anchor: e.currentTarget,
                    sourceId: i.sourceId,
                    join: {
                      to: sources
                        .find((s) => s.id === i.sourceId)
                        ?.parsed?.columns?.find((c) => c.name === i.field)?.ref,
                      field: i.field,
                      present: i.present !== false,
                    },
                  })
                }
              >
                Supply the records
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => omitRows(i)}
              >
                Leave those rows out
              </Button>
            </Stack>
          )}
          {i.kind === "personal" && (
            <Box sx={{ mt: 0.75 }}>
              <PersonalDataChoice
                compact
                suggested={i.suggested}
                choice={i.choice}
                readOnly={!gates.canEdit}
                onChoose={(c) =>
                  patch(
                    personalChoicePatch(
                      latest.current,
                      sources.find((s) => s.id === i.sourceId) ||
                        (latest.current.sources || []).find(
                          (s) => s.id === i.sourceId,
                        ),
                      c,
                    ),
                  )
                }
              />
            </Box>
          )}
        </Box>
      </Stack>
    );
  };

  if (rejected)
    return (
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        Left the environment as it is. The files stay under World &amp; tools →
        Sources if you want them later.
      </Typography>
    );
  if (saved)
    return (
      <Stack spacing={0.5}>
        <Typography sx={{ typography: "s2" }}>
          {saved.inPlace
            ? `Added to environment ${saved.label}. No run has used it yet, so the changes apply now.`
            : saved.draft
              ? "Added to your unsaved changes. Review and save them from the header."
              : `Building environment ${saved.label}${saved.rules ? ` with ${saved.rules} new rule${saved.rules === 1 ? "" : "s"}` : ""} — the page shows its progress. Proposed scenarios go to the Scenarios tab to review. Runs on ${saved.label} pin this data; earlier runs keep theirs.`}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() => onOpenSource?.(null)}
          sx={{ alignSelf: "flex-start" }}
        >
          Open Sources
        </Button>
      </Stack>
    );

  return (
    <Box
      sx={
        drawer
          ? {
              display: "flex",
              flexDirection: "column",
              height: "100%",
              minHeight: 0,
            }
          : { border: "1px solid", borderColor: "divider", borderRadius: 1 }
      }
    >
      {!drawer && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Proposed changes
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Nothing changes until you add them.
          </Typography>
        </Box>
      )}
      <Box
        sx={drawer ? { flex: 1, minHeight: 0, overflowY: "auto" } : undefined}
      >
        {GROUPS.flatMap((g) => {
          const items = proposal.items.filter((x) => g.kinds.includes(x.kind));
          if (!items.length) return [];
          return [
            <Typography
              key={`h-${g.label}`}
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                px: drawer ? 2.5 : 1.5,
                pt: 1,
                pb: 0.25,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {`${g.label} · ${items.length}`}
            </Typography>,
            ...items.map((x) => renderItem(x)),
          ];
        })}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: drawer ? 2.5 : 1.5,
          /* Clear of the floating assistant button in the corner. */
          pr: drawer ? 9 : 1.5,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {undecided.length
            ? "Choose how to handle personal data first"
            : proposal.items.some((i) => i.blocking)
              ? "Blocked items stay out"
              : inPlace
                ? `Applies to ${current} now — no run uses it yet`
                : `Saved as ${next} from Review changes`}
        </Typography>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setRejected(true)}
        >
          Reject
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={undecided.length > 0 || (!count && !hasChoices)}
          onClick={() =>
            count ? accept() : onSaved ? onSaved() : setRejected(true)
          }
        >
          {!count
            ? "Done"
            : inPlace
              ? `Add ${count} to ${current}`
              : `Add ${count} ${count === 1 ? "change" : "changes"}`}
        </Button>
      </Stack>
      <AddSourceMenu
        anchorEl={supply?.anchor}
        onClose={() => setSupply((c) => c && { ...c, anchor: null })}
        onAddFiles={supplyFiles}
        onAddOrg={supplyOrg}
        added={(envState.sources || [])
          .filter((s) => s.kb && !s.archived)
          .map((s) => s.name)}
        title={`Add the file that holds ${supply?.join?.to?.split(".")[0] || "the missing records"}.`}
      />
    </Box>
  );
}

DataProposalCard.propTypes = {
  onSaved: PropTypes.func,
  env: PropTypes.object.isRequired,
  sourceIds: PropTypes.array.isRequired,
  onOpenSource: PropTypes.func,
  layout: PropTypes.oneOf(["card", "drawer"]),
};
