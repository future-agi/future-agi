import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { castFor } from "src/api/simulate-environments/_fixtures/actors";
import SectionCard from "../../components/SectionCard";
import useUiSample from "../../hooks/useUiSample";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import { realToolArgs } from "../world/ToolArgsGrid";
import ActorsPanel from "./ActorsPanel";
import ContractEditDialog from "./ContractEditDialog";
import { ArgLimitsSection, RulesSection, UseCasesSection } from "./ContractSections";
import { CONTRACT_COPY } from "./contract.constants";

// Map the real §6 `end_conditions` onto the card's shape. Returns null when the
// environment carries no end conditions yet, and the card says so.
// `ended_reasons` is the vocabulary a finished call may carry (it ends); the
// two limits are the conversation's ceilings (it's cut off).
const CLOCK = {
  "real-time": { mode: "Real time", note: "Pauses and slow replies are part of the test, so time runs as it would on a real call." },
  stepped: { mode: "Stepped", note: "The chat advances a turn at a time, so a slow reply costs nothing." },
};
// 3600 → "60 minutes", 45 → "45 seconds", as Vel words the limit.
const durationWords = (sec) =>
  sec % 60 === 0 ? `${sec / 60} minute${sec === 60 ? "" : "s"}` : `${sec} seconds`;
function realEpisode(env) {
  const ec = env?.endConditions;
  if (!ec) return null;
  const truncate = [
    ec.max_turns != null && {
      when: `${ec.max_turns} turns`,
      note: "Ceiling across the suite. Each scenario carries its own.",
    },
    ec.max_duration_seconds != null && {
      when: durationWords(ec.max_duration_seconds),
      note: "The job's wall-clock limit.",
    },
  ].filter(Boolean);
  return {
    terminate: (ec.ended_reasons || []).map((reason) => ({ when: reason })),
    truncate,
    clock: CLOCK[ec.clock] || { mode: ec.clock || "-", note: "How run time is measured." },
  };
}

// The seed as sent, or null when JSON.parse has already rounded it: settings.seed
// is often past 2^53, and a rounded seed would replay a different run.
const exactSeed = (seed) =>
  typeof seed === "string" || Number.isSafeInteger(seed) ? String(seed) : null;

// An argument is a limit when the contract says which values it allows.
const limitsOf = (tools = []) =>
  tools.flatMap((t) => realToolArgs(t).filter((a) => a.values?.length).map((a) => ({ ...a, tool: t.name })));

/**
 * The environment contract as Vel lays it out: the rules, the argument
 * limits, the use cases, when a conversation ends, and the actors. Rules, use
 * cases, limits, end conditions and the seed are real. Editing them, rule
 * coverage, number ranges and actors have no API yet: edits are flagged UI
 * only and save nothing, and ?uiSample=actors shows Vel's cast. The world a
 * run executes against lives on the World tab.
 */
export default function RlContractPanel({ env, envState, patch, onGo, locked = false, backed = false }) {
  const sampleActors = useUiSample("actors");
  const [editing, setEditing] = useState(null);
  const [sampleCast, setSampleCast] = useState(() => castFor(env));
  const episode = realEpisode(env);
  const canEdit = !!patch && !locked;
  const onEdit = canEdit ? setEditing : null;
  const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  const rules = env?.rules || [];
  const useCases = env?.useCases || [];
  const limits = limitsOf(env?.tools);
  const seed = exactSeed(env?.runSeed);

  return (
    <Box sx={{ p: 2 }}>
      <RulesSection rules={rules} onEdit={onEdit} />
      {limits.length > 0 && <ArgLimitsSection limits={limits} onEdit={onEdit} onGo={onGo} />}
      {(useCases.length > 0 || canEdit) && <UseCasesSection useCases={useCases} onEdit={onEdit} />}

      <SectionCard
        title={CONTRACT_COPY.endConditions.title}
        subtitle={CONTRACT_COPY.endConditions.subtitle}
        sx={{ mb: 2 }}
      >
        {!episode ? (
          <Box sx={{ px: 2.5, py: 2.25 }}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{CONTRACT_COPY.endConditions.notAuthored}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {backed ? CONTRACT_COPY.endConditions.notAuthoredBuilt : CONTRACT_COPY.endConditions.notAuthoredUnbuilt}
            </Typography>
          </Box>
        ) : (
          <>
            <Box sx={{ display: "grid", gap: "1px", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, bgcolor: "divider" }}>
              <EndColumn label="It ends" note="The conversation is over, and its checks decide the result." items={episode.terminate} />
              <EndColumn label="It's cut off" note="A limit stops the conversation before it's over." items={episode.truncate} />
            </Box>
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />} sx={{ borderTop: "1px solid", borderColor: "divider" }}>
              <EndRow icon="solar:clock-circle-linear" title={`Time · ${episode.clock.mode}`} body={episode.clock.note} />
              <EndRow
                icon="solar:dice-linear"
                title="Same starting data every time"
                body={`Every conversation starts from the same data. Run it again and the world, the caller's turns and the tool answers come back the same, so a failing scenario can be replayed and debugged.${seed ? ` Seed ${seed}.` : ""}`}
              />
            </Stack>
          </>
        )}
      </SectionCard>

      <ActorsPanel
        env={env}
        envState={{ ...envState, actors: sampleActors ? sampleCast : [] }}
        patch={
          patch
            ? (p) => {
                if (sampleActors && p?.actors) setSampleCast(p.actors);
                uiOnly();
              }
            : undefined
        }
        onGo={onGo}
        locked={locked}
        onSave={uiOnly}
      />

      {editing && (
        <ContractEditDialog
          edit={editing}
          onClose={() => setEditing(null)}
          onApply={() => {
            setEditing(null);
            uiOnly();
          }}
        />
      )}
    </Box>
  );
}

const ENV_SHAPE = PropTypes.shape({
  surface: PropTypes.string,
  tools: PropTypes.array,
  rules: PropTypes.arrayOf(PropTypes.string),
  useCases: PropTypes.arrayOf(PropTypes.string),
  endConditions: PropTypes.object,
  runSeed: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
});

RlContractPanel.propTypes = {
  env: ENV_SHAPE.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  onGo: PropTypes.func,
  locked: PropTypes.bool,
  // A built (harness) environment; a template or fork is never built.
  backed: PropTypes.bool,
};

function EndColumn({ label, note, items }) {
  return (
    <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
      <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle", mb: 1 }}>{label}</Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.25 }}>{note}</Typography>
      <Stack spacing={1}>
        {items.map((e) => (
          <Box key={e.when}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{e.when}</Typography>
            {e.note && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{e.note}</Typography>}
          </Box>
        ))}
        {items.length === 0 && <Typography sx={{ typography: "s3", color: "text.subtitle" }}>-</Typography>}
      </Stack>
    </Box>
  );
}
EndColumn.propTypes = { label: PropTypes.string, note: PropTypes.string, items: PropTypes.array };

function EndRow({ icon, title, body }) {
  return (
    <Stack direction="row" spacing={1.75} alignItems="flex-start" sx={{ px: 2.5, py: 1.75 }}>
      <Iconify icon={icon} width={16} sx={{ color: "text.subtitle", flexShrink: 0, mt: "1px" }} />
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{title}</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{body}</Typography>
      </Box>
    </Stack>
  );
}
EndRow.propTypes = { icon: PropTypes.string, title: PropTypes.string, body: PropTypes.string };
