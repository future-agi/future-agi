import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useEnvState } from "../../store";
import { useRoleGates } from "../../_mock/previewRole";
import {
  acceptDataPatch,
  dataProposalOf,
  sourceEditPatch,
} from "../../_mock/orgData";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";
import { personaFor } from "../../_mock/scenarios";
import { stampNewBatch } from "../../_mock/addScenarios";
import { asProposal } from "../../_mock/scenarioSources";
import { neutralCheckboxSx } from "../../components/primitives";
import { castOf } from "../../_mock/actors";

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
};

/* Grouped by what each change touches — the same areas as Review changes. */
const GROUPS = [
  { label: "Rules", kinds: ["conflict", "rule"] },
  { label: "Starting state and records", kinds: ["table", "state", "missing"] },
  { label: "Tools", kinds: ["tool"] },
  { label: "Actors", kinds: ["actor"] },
  { label: "Scenarios", kinds: ["scenario"] },
  { label: "Who sees what, and problems", kinds: ["hidden", "unreadable"] },
];

/**
 * What the builder proposes from the attached files (roadmap data.md §1
 * "Review changes"): each rule, table and case beside the passage or row it
 * came from, inferences labelled, conflicts and missing data as choices that
 * block only what depends on them. Accept selected saves one environment
 * version — the data snapshot runs will pin.
 */
export default function DataProposalCard({ env, sourceIds, onOpenSource }) {
  const { envState, patch } = useEnvState(env.id);
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
      }),
    [sources, cast, env.tools],
  );
  /* An item's text changed before accepting: a rule's wording, an approval scope. */
  const [edits, setEdits] = useState({});
  const [editing, setEditing] = useState(null);
  const editValueOf = (i) =>
    edits[i.id] ??
    (i.kind === "rule" ? i.rule : i.actor?.[i.editable?.field] || "");
  const [picked, setPicked] = useState(() =>
    proposal.items
      .filter((i) => !i.blocking && i.kind !== "hidden")
      .map((i) => i.id),
  );
  const [conflictPick, setConflictPick] = useState(null);
  const [saved, setSaved] = useState(null);
  const [rejected, setRejected] = useState(false);
  const next = nextSnapshotLabel(env, envState);
  const pickable = (i) =>
    !i.blocking && !["hidden", "unreadable"].includes(i.kind);
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
    const p = acceptDataPatch(env, envState, proposal, picked, {
      conflictPick,
      scenarioRows,
      edits,
      cast,
    });
    const { ruleIds, ...rest } = p;
    patch(rest);
    setSaved({ label: next, rules: ruleIds.length });
    return null;
  };
  const omitRows = (item) =>
    patch(
      sourceEditPatch(envState, item.sourceId, {
        joins: {
          ...(sources.find((s) => s.id === item.sourceId)?.joins || {}),
          [item.field]: "omit",
        },
      }),
    );

  const renderItem = (i) => {
    const k = KIND[i.kind];
    const on = picked.includes(i.id);
    return (
      <Stack
        key={i.id}
        direction="row"
        spacing={1}
        sx={{
          px: 1,
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
          sx={{ ...neutralCheckboxSx, p: 0.25, alignSelf: "flex-start" }}
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
              <ButtonBase
                onClick={() => setEditing(editing === i.id ? null : i.id)}
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  "&:hover": { color: "text.primary" },
                }}
              >
                {editing === i.id ? "Done" : "Edit"}
              </ButtonBase>
            )}
            {i.inferred && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                · inferred
              </Typography>
            )}
          </Stack>
          {i.detail && !(i.editable && edits[i.id] != null) && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
            >
              {i.detail}
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
          {i.cite && (
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
              {i.cite.excerpt ? ` — “${i.cite.excerpt}”` : ""}
            </ButtonBase>
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
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Attach the missing file in chat, or
              </Typography>
              <ButtonBase
                onClick={() => omitRows(i)}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  textDecoration: "underline",
                }}
              >
                leave those rows out
              </ButtonBase>
            </Stack>
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
          {`Saved as environment ${saved.label}. ${saved.rules ? `${saved.rules} rule${saved.rules === 1 ? "" : "s"} added to the contract; ` : ""}proposed scenarios are on the Scenarios tab to review. Runs on ${saved.label} pin this data snapshot; earlier runs keep theirs.`}
        </Typography>
        <ButtonBase
          onClick={() => onOpenSource?.(null)}
          sx={{
            alignSelf: "flex-start",
            typography: "s2",
            fontWeight: 600,
            textDecoration: "underline",
          }}
        >
          Open Sources
        </ButtonBase>
      </Stack>
    );

  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
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
          Each one cites where it came from. Nothing changes until you accept.
        </Typography>
      </Box>
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
              px: 1.5,
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
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 1.5,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {proposal.items.some((i) => i.blocking)
            ? "Blocked items stay out until they're resolved; the rest can be saved now."
            : `Saves environment ${next} with this data snapshot.`}
        </Typography>
        <Button
          size="small"
          onClick={() => setRejected(true)}
          sx={{ color: "text.secondary" }}
        >
          Reject
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!count}
          onClick={accept}
        >
          {`Accept ${count} and save as ${next}`}
        </Button>
      </Stack>
    </Box>
  );
}

DataProposalCard.propTypes = {
  env: PropTypes.object.isRequired,
  sourceIds: PropTypes.array.isRequired,
  onOpenSource: PropTypes.func,
};
