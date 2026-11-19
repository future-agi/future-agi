import PropTypes from "prop-types";
import { createElement, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
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
import { nextEnvLabel, currentEnvSnapshot } from "../../_mock/envSnapshot";
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
  reference: { icon: "solar:book-linear", label: "Background" },
  personal: { icon: "solar:shield-user-linear", label: "Personal data" },
};

/* Grouped by what each change touches — the same areas as Review changes. */
const GROUPS = [
  { label: "Rules", kinds: ["conflict", "rule"] },
  { label: "Starting state and records", kinds: ["table", "state", "missing"] },
  { label: "Tools", kinds: ["tool"] },
  { label: "Actors", kinds: ["actor"] },
  { label: "Scenarios", kinds: ["scenario"] },
  { label: "Background for the agent", kinds: ["reference"] },
  { label: "Personal data — your choice", kinds: ["personal"] },
  {
    label: "Kept from the agent, and problems",
    kinds: ["hidden", "unreadable"],
  },
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
  const next = nextEnvLabel(env, envState);
  /* No run has used the current version yet: accepted changes land on it
     now, so there's no next version to save (versions.js envChangePatch). */
  const inPlace = editsLandInPlace(envState);
  const current =
    currentEnvSnapshot(env, envState)?.envLabel ||
    currentEnvVersion(env, envState)?.label ||
    "the current version";
  /* A single pickable change needs no checkbox beside Add / Reject. */
  const onlyOne =
    proposal.items.filter(
      (i) =>
        !i.blocking && !["hidden", "unreadable", "personal"].includes(i.kind),
    ).length === 1;
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
    /* Counted like the button: what was picked, less what couldn't apply. */
    const n = Math.max(0, count - failed.length);
    const what = `${n}${failed.length ? ` of ${count}` : ""} change${(failed.length ? count : n) === 1 ? "" : "s"}`;
    if (inPlace) {
      enqueueSnackbar(
        `Added ${what} to environment ${current}${failed.length ? ` · ${failed.length} couldn't apply` : ""}. No run has used ${current} yet, so they apply now.`,
        { variant: "success" },
      );
      if (onSaved) onSaved();
      else setSaved({ label: current, rules: 0, inPlace: true });
      return null;
    }
    enqueueSnackbar(
      `Added ${what} to your unsaved changes${failed.length ? ` · ${failed.length} couldn't apply` : ""}. Review and save them to make the version.`,
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
      `Copied ${o.name} into the draft as it is now. Nothing reaches the world until you accept.`,
      { variant: "success" },
    );
  };

  const renderItem = (i) => {
    const k = KIND[i.kind];
    const on = picked.includes(i.id);
    /* A whole file joining as reference: facts, then what it says. */
    if (i.kind === "reference")
      return (
        <ReferenceItem
          key={i.id}
          item={i}
          inset={drawer ? 2.5 : 1.5}
          onOpen={() => onOpenSource?.(i.cite?.sourceId)}
        />
      );
    return (
      <Stack
        key={i.id}
        direction="row"
        spacing={1.25}
        sx={{
          px: drawer ? 2.5 : 1.5,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
          "& .pi-actions": { opacity: editing === i.id ? 1 : 0 },
          "&:hover .pi-actions, &:focus-within .pi-actions": { opacity: 1 },
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
            p: 0,
            mt: "1px",
            alignSelf: "flex-start",
            /* A choice, or a whole file joining as reference — neither is a
               change to pick; Add / Reject decide it. */
            ...(["personal", "reference"].includes(i.kind) && {
              visibility: "hidden",
            }),
            /* One change only: Add / Reject already decide it. */
            ...(onlyOne && { display: "none" }),
          }}
        />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} alignItems="flex-start">
            {i.blocking && (
              <Iconify
                icon={k.icon}
                width={14}
                sx={{ color: "status.warning.fg", flexShrink: 0, mt: "2px" }}
              />
            )}
            {/* The group heading names the kind, so a rule reads as the rule. */}
            <Typography
              sx={{ typography: "s1", fontWeight: 500, flex: 1, minWidth: 0 }}
            >
              {i.kind === "rule"
                ? edits[i.id] ?? i.rule ?? i.title.replace(/^Rule:\s*/, "")
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
                {editing === i.id ? (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => setEditing(null)}
                    sx={{
                      minWidth: 0,
                      py: 0,
                      px: 1,
                      typography: "s3",
                      color: "text.primary",
                      borderColor: "divider",
                      flexShrink: 0,
                    }}
                  >
                    Done
                  </Button>
                ) : (
                  <IconButton
                    size="small"
                    className="pi-actions"
                    aria-label={i.kind === "rule" ? "Edit rule" : "Edit"}
                    onClick={() => setEditing(i.id)}
                    sx={{
                      p: 0.25,
                      color: "text.tertiary",
                      flexShrink: 0,
                      "&:hover": { color: "text.primary" },
                    }}
                  >
                    <Iconify icon="solar:pen-linear" width={14} />
                  </IconButton>
                )}
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
            <>
              {i.cite.excerpt &&
                i.cite.excerpt.trim() !== String(i.rule || "").trim() &&
                i.kind !== "table" && (
                  <Typography
                    sx={{
                      mt: 0.5,
                      pl: 1,
                      borderLeft: "1px solid",
                      borderColor: "divider",
                      typography: "s3",
                      color: "text.secondary",
                    }}
                  >
                    {`“${i.cite.excerpt}”`}
                  </Typography>
                )}
              <ButtonBase
                onClick={() => onOpenSource?.(i.cite.sourceId)}
                sx={{
                  mt: 0.5,
                  typography: "s3",
                  color: "text.tertiary",
                  textAlign: "left",
                  display: "block",
                  "&:hover": {
                    color: "text.primary",
                    textDecoration: "underline",
                  },
                }}
              >
                {`From ${i.cite.ref}`}
              </ButtonBase>
            </>
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
        {GROUPS.filter((g) =>
          proposal.items.some((x) => g.kinds.includes(x.kind)),
        ).flatMap((g, gi) => {
          const items = proposal.items.filter((x) => g.kinds.includes(x.kind));
          return [
            /* Design system section heading: 13/500 with the count; one
               hairline between groups, none under the drawer's own header. */
            <Typography
              key={`h-${g.label}`}
              sx={{
                typography: "m3",
                fontWeight: 500,
                color: "text.primary",
                px: drawer ? 2.5 : 1.5,
                pt: gi ? 2 : 1.5,
                pb: 0.75,
                ...(gi > 0 && {
                  borderTop: "1px solid",
                  borderColor: "divider",
                }),
              }}
            >
              {g.label}
              <Box
                component="span"
                sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
              >
                {items.length}
              </Box>
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
                : `Saved as environment ${next} from Review changes`}
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

/* A file joining as reference material (data.md "Policy vs reference vs
   records"): a key-value spec — file, how it's used, rules, how much was
   read — then the text itself as a hairline table, each section named once.
   No checkbox: Add / Reject below decide it. */
function ReferenceItem({ item, inset, onOpen }) {
  const [all, setAll] = useState(false);
  const read = item.read || { sections: 0, words: 0, passages: [] };
  const rows = all ? read.passages : read.passages.slice(0, 6);
  const facts = [
    [
      "File",
      <ButtonBase
        key="f"
        onClick={onOpen}
        sx={{
          typography: "s1",
          fontWeight: 500,
          color: "text.primary",
          textDecoration: "underline",
          textUnderlineOffset: 3,
          textDecorationColor: (t) => t.palette.text.disabled,
          "&:hover": { textDecorationColor: "currentColor" },
        }}
      >
        {item.cite?.ref}
      </ButtonBase>,
    ],
    ["Used as", "Background — the agent can look things up in it"],
    ["Rules", "None stated, so nothing is graded from it"],
    [
      "Read",
      read.passages.length
        ? `${read.sections} section${read.sections === 1 ? "" : "s"} · ${read.words.toLocaleString()} words`
        : "No readable text",
    ],
  ];
  return (
    <Box sx={{ px: inset, pb: 2 }}>
      <Box component="table" sx={{ borderCollapse: "collapse", mb: 1.5 }}>
        <Box component="tbody">
          {facts.map(([k, v]) => (
            <Box component="tr" key={k}>
              <Box
                component="td"
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 88,
                  py: 0.5,
                  pr: 1.5,
                  verticalAlign: "top",
                }}
              >
                {k}
              </Box>
              <Box
                component="td"
                sx={{ typography: "s1", color: "text.primary", py: 0.5 }}
              >
                {v}
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
      {read.passages.length > 0 && (
        <>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "80px minmax(0, 1fr)",
              borderTop: "1px solid",
              borderColor: "divider",
              "& > *": {
                borderBottom: "1px solid",
                borderColor: "divider",
                py: 0.75,
              },
            }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Section
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              What it says
            </Typography>
            {rows.map((x, n) => [
              <Typography
                key={`${x.id}-w`}
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {n === 0 || rows[n - 1].where !== x.where
                  ? x.where.replace(/^section /, "")
                  : ""}
              </Typography>,
              <Typography
                key={`${x.id}-t`}
                sx={{ typography: "s1", color: "text.secondary" }}
              >
                {x.text}
              </Typography>,
            ])}
          </Box>
          {read.passages.length > 6 && (
            <ButtonBase
              onClick={() => setAll((v) => !v)}
              sx={{
                mt: 0.75,
                typography: "s1",
                fontWeight: 500,
                color: "text.secondary",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              {all ? "Show fewer" : `Show all ${read.passages.length} lines`}
            </ButtonBase>
          )}
        </>
      )}
    </Box>
  );
}
ReferenceItem.propTypes = {
  item: PropTypes.object.isRequired,
  inset: PropTypes.number,
  onOpen: PropTypes.func,
};
