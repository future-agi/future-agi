import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import useSimulationAccess from "../../hooks/useSimulationAccess";
import { WORKSPACE_COPY } from "../workspace.constants";
import { Label } from "./ContractPart";
import ContractListCard from "./ContractListCard";
import ContractEditDialog from "./ContractEditDialog";
import { contractInstruction } from "./contractInstructions";
import {
  CONTRACT_COPY,
  CONTRACT_EDIT_COPY,
  CONTRACT_LISTS_COPY,
} from "./contract.constants";

const EC = CONTRACT_COPY.endConditions;

function durationLabel(seconds) {
  if (seconds >= 60 && seconds % 60 === 0) {
    const minutes = seconds / 60;
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  return `${seconds} ${seconds === 1 ? "second" : "seconds"}`;
}

function realEpisode(env) {
  const ec = env?.endConditions;
  if (!ec) return null;
  const cutOff = [
    ec.max_turns != null && {
      when: EC.turns(ec.max_turns),
      note: EC.turnsNote,
    },
    ec.max_duration_seconds != null && {
      when: durationLabel(ec.max_duration_seconds),
      note: EC.durationNote,
    },
  ].filter(Boolean);
  const reasonRows = (reasons) =>
    (reasons || []).map((reason) => ({ when: reason, note: "" }));
  return {
    ends: reasonRows(ec.terminate_reasons ?? ec.ended_reasons),
    cutOff: [...reasonRows(ec.truncate_reasons), ...cutOff],
    seed: ec.seed
      ? {
          title: EC.seedTitle,
          note: EC.seedNote(ec.seed.value, ec.seed.controls || []),
        }
      : null,
    clock: {
      mode: EC.clockMode[ec.clock] || ec.clock || "-",
      note: EC.clockNote[ec.clock] || EC.clockNoteDefault,
    },
  };
}

export default function RlContractPanel({
  env,
  backed = false,
  locked = false,
  chat = null,
  scenarios = [],
  onGo,
}) {
  const { canEdit } = useSimulationAccess();
  const [editing, setEditing] = useState(null);
  const episode = realEpisode(env);
  const rules = (env?.rules || []).map(String).filter(Boolean);
  const useCases = (env?.useCases || []).map(String).filter(Boolean);

  const showActions = !locked && typeof chat?.send === "function";
  const coverageOf = (text) => {
    const subGoal = (env?.ruleDetails || []).find(
      (detail) => detail?.text === text,
    )?.sub_goal;
    if (!subGoal) return { subGoals: 0, scenarios: 0 };
    return {
      subGoals: 1,
      scenarios: scenarios.filter((row) =>
        (row?.subTasks || []).some((task) => task.id === subGoal),
      ).length,
    };
  };
  const blockedReason = !canEdit
    ? WORKSPACE_COPY.viewer.edit
    : !chat?.live
      ? CONTRACT_LISTS_COPY.notLive
      : chat?.busy
        ? CONTRACT_LISTS_COPY.busy
        : null;

  const open = (target, op) => (index, text) =>
    setEditing({ target, op, index, text: text ?? "" });

  const confirm = (next) => {
    const instruction = contractInstruction({ ...editing, next });
    setEditing(null);
    if (!instruction || blockedReason) return;
    chat.send(instruction);
    enqueueSnackbar(CONTRACT_EDIT_COPY.sent, { variant: "info" });
  };

  return (
    <Box sx={{ p: 2 }}>
      <ContractListCard
        list={CONTRACT_LISTS_COPY.rules}
        items={rules}
        coverageOf={coverageOf}
        onGo={onGo}
        showActions={showActions}
        blockedReason={blockedReason}
        onAdd={() => open("rule", "add")()}
        onEdit={open("rule", "edit")}
        onRemove={open("rule", "remove")}
        sx={{ mb: 2 }}
      />

      {(useCases.length > 0 || showActions) && (
        <ContractListCard
          list={CONTRACT_LISTS_COPY.useCases}
          items={useCases}
          showActions={showActions}
          blockedReason={blockedReason}
          onAdd={() => open("useCase", "add")()}
          onEdit={open("useCase", "edit")}
          onRemove={open("useCase", "remove")}
          sx={{ mb: 2 }}
        />
      )}

      <SectionCard roomy title={EC.title} subtitle={EC.subtitle} sx={{ mb: 2 }}>
        {!episode ? (
          <Box sx={{ px: 2.5, py: 1.75 }}>
            <Typography sx={{ typography: "s1", fontWeight: 500 }}>
              {EC.notAuthored}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {backed ? EC.notAuthoredBuilt : EC.notAuthoredUnbuilt}
            </Typography>
          </Box>
        ) : (
          <>
            <Box
              sx={{
                display: "grid",
                gap: "1px",
                gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                bgcolor: "action.selected",
              }}
            >
              <EndingColumn
                label={EC.endsLabel}
                blurb={EC.endsBlurb}
                rows={episode.ends}
                none={EC.endsNone}
              />
              <EndingColumn
                label={EC.cutLabel}
                blurb={EC.cutBlurb}
                rows={episode.cutOff}
                none={EC.cutNone}
              />
            </Box>
            <FooterRow
              icon="solar:clock-circle-linear"
              title={EC.timeLabel(episode.clock.mode)}
              note={episode.clock.note}
            />
            {episode.seed && (
              <FooterRow
                icon="solar:dice-linear"
                title={episode.seed.title}
                note={episode.seed.note}
              />
            )}
          </>
        )}
      </SectionCard>

      {editing && (
        <ContractEditDialog
          edit={editing}
          onClose={() => setEditing(null)}
          onConfirm={confirm}
        />
      )}
    </Box>
  );
}

function FooterRow({ icon, title, note }) {
  return (
    <Stack
      direction="row"
      spacing={1.75}
      alignItems="flex-start"
      sx={{ px: 2.5, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.tertiary", flexShrink: 0, mt: "1px" }}
      />
      <Box minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {note}
        </Typography>
      </Box>
    </Stack>
  );
}

FooterRow.propTypes = {
  icon: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  note: PropTypes.string.isRequired,
};

function EndingColumn({ label, blurb, rows, none }) {
  return (
    <Box sx={{ p: 2.5, bgcolor: "background.paper" }}>
      <Label>{label}</Label>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1.25 }}>
        {blurb}
      </Typography>
      {rows.length === 0 ? (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {none}
        </Typography>
      ) : (
        <Stack spacing={1}>
          {rows.map((row) => (
            <Box key={row.when}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {row.when}
              </Typography>
              {row.note && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {row.note}
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
}

EndingColumn.propTypes = {
  label: PropTypes.string.isRequired,
  blurb: PropTypes.string.isRequired,
  rows: PropTypes.arrayOf(
    PropTypes.shape({ when: PropTypes.string, note: PropTypes.string }),
  ).isRequired,
  none: PropTypes.string.isRequired,
};

RlContractPanel.propTypes = {
  env: PropTypes.shape({
    rules: PropTypes.arrayOf(PropTypes.string),
    useCases: PropTypes.arrayOf(PropTypes.string),
    ruleDetails: PropTypes.arrayOf(PropTypes.object),
    useCaseDetails: PropTypes.arrayOf(PropTypes.object),
    subGoals: PropTypes.arrayOf(PropTypes.object),
    endConditions: PropTypes.shape({
      max_turns: PropTypes.number,
      max_duration_seconds: PropTypes.number,
      clock: PropTypes.string,
      ended_reasons: PropTypes.arrayOf(PropTypes.string),
      terminate_reasons: PropTypes.arrayOf(PropTypes.string),
      truncate_reasons: PropTypes.arrayOf(PropTypes.string),
      seed: PropTypes.shape({
        value: PropTypes.string,
        controls: PropTypes.arrayOf(PropTypes.string),
      }),
    }),
  }).isRequired,
  backed: PropTypes.bool,
  locked: PropTypes.bool,
  scenarios: PropTypes.arrayOf(PropTypes.object),
  onGo: PropTypes.func,
  chat: PropTypes.shape({
    send: PropTypes.func,
    busy: PropTypes.bool,
    live: PropTypes.bool,
  }),
};
