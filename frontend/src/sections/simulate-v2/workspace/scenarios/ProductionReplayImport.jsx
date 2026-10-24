import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Collapse,
  IconButton,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../../components/primitives";
import {
  productionConversationsOf,
  replayScenarioOf,
  asProposal,
  REPLAY_NOTE,
} from "../../_mock/scenarioSources";
import { relativeTime } from "../../_mock/scenarioProvenance";
import { PersonalDataTag } from "./ScenarioStateBits";
import { clickable, focusRing } from "../../components/clickable";

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};
const fmtDuration = (s) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Add scenarios from production, word for word (Phase 2).
 *
 * A production conversation's caller turns become a scenario the simulated
 * caller replays verbatim — the exact words a real caller used, which a
 * generated caller would paraphrase away. Personal data is replaced with
 * fake values before the scenario is saved, and the call audio is never
 * reused: the simulated caller speaks the words in its own voice.
 */
export default function ProductionReplayImport({ env, onAdd, selected }) {
  const conversations = useMemo(() => productionConversationsOf(env), [env]);
  /* What each becomes, already scrubbed — the only form shown or saved. */
  const drafts = useMemo(
    () => new Map(conversations.map((c) => [c.id, replayScenarioOf(c, env)])),
    [conversations, env],
  );
  const alreadyIn = useMemo(
    () => new Set((selected || []).map((s) => s.id)),
    [selected],
  );
  const [picked, setPicked] = useState({});
  const open = conversations.filter((c) => !alreadyIn.has(drafts.get(c.id).id));
  const chosen = open.filter((c) => picked[c.id]);
  const commit = () =>
    onAdd(chosen.map((c) => asProposal(drafts.get(c.id), "production")));

  return (
    <SectionCard
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Production conversations, replayed word for word
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {REPLAY_NOTE}
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={commit}
          startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
          sx={{ whiteSpace: "nowrap", flexShrink: 0, minWidth: 148 }}
        >
          {chosen.length
            ? `Add ${chosen.length} replay${chosen.length === 1 ? "" : "s"}`
            : "Add replays"}
        </Button>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2.5,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {`${conversations.length} recent conversations · shown with personal data already replaced`}
        </Typography>
        <Button
          size="small"
          disabled={open.length === 0 || open.every((c) => picked[c.id])}
          onClick={() =>
            setPicked(Object.fromEntries(open.map((c) => [c.id, true])))
          }
          sx={{
            color: "primary.main",
            minWidth: 0,
            "&.Mui-disabled": { color: "text.disabled" },
          }}
        >
          Select all
        </Button>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {conversations.map((c) => (
          <ConversationRow
            key={c.id}
            conv={c}
            draft={drafts.get(c.id)}
            added={alreadyIn.has(drafts.get(c.id).id)}
            picked={!!picked[c.id]}
            onToggle={() => setPicked((p) => ({ ...p, [c.id]: !p[c.id] }))}
          />
        ))}
      </Box>
    </SectionCard>
  );
}
ProductionReplayImport.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};

function ConversationRow({ conv, draft, added, picked, onToggle }) {
  const [open, setOpen] = useState(false);
  const turns = draft.productionReplay?.turns || [];
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{
          ...focusRing,
          px: 1.5,
          py: 1,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Box onClick={(e) => e.stopPropagation()}>
          <Checkbox
            size="small"
            checked={picked || added}
            disabled={added}
            onChange={onToggle}
            sx={CHECKBOX_SX}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            noWrap
            sx={{
              typography: "s2",
              fontWeight: 500,
              color: added ? "text.disabled" : "text.primary",
            }}
          >
            {conv.topic}
          </Typography>
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{ mt: 0.25, minWidth: 0 }}
          >
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              {`${conv.channel} · ${fmtDuration(conv.durationS)} · ${relativeTime(conv.at)} · ${turns.length} caller turns${conv.outcome === "failed" ? " · failed in production" : ""}`}
            </Typography>
            <PersonalDataTag row={draft} />
            {added && (
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                · already added
              </Typography>
            )}
          </Stack>
        </Box>
        <Tooltip
          title={open ? "Hide the caller's turns" : "Show the caller's turns"}
          arrow
        >
          <IconButton size="small">
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={0.5}
          sx={{ px: 6, py: 1.5, bgcolor: "background.neutral" }}
        >
          {turns.map((t) => (
            <Stack
              key={t.n}
              direction="row"
              spacing={1.25}
              alignItems="baseline"
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.disabled",
                  width: 52,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`Caller ${t.n}`}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {t.text}
              </Typography>
            </Stack>
          ))}
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", pt: 0.5 }}
          >
            {`Persona inferred: ${draft.persona?.name} · ${draft.persona?.archetype}. The agent's side isn't replayed — your agent answers for itself.`}
          </Typography>
        </Stack>
      </Collapse>
    </Box>
  );
}
ConversationRow.propTypes = {
  conv: PropTypes.object,
  draft: PropTypes.object,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};
