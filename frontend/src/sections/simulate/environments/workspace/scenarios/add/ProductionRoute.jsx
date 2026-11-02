import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Collapse,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_PRODUCTION_CONVERSATIONS,
  SAMPLE_REPLAY_NOTE,
  personalDataDetail,
  personalDataLine,
  replayScenarioId,
} from "src/api/simulate-environments/_fixtures/detail/scenarioSources";
import SectionCard from "../../../components/SectionCard";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "../scenarios.shapes";

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};

const fmtDuration = (s) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// Vel's "Production, word for word": a production conversation's caller turns
// become a scenario the simulated caller replays verbatim. Personal data is
// replaced before anything is shown and the call audio is never reused. No
// backend yet: the conversations are sample data and adding saves nothing.
export default function ProductionRoute({ env, envState, onClose }) {
  const conversations = SAMPLE_PRODUCTION_CONVERSATIONS;
  const channel = env?.surface === "voice" ? "Phone call" : "Chat";
  const alreadyIn = useMemo(
    () => new Set((envState?.scenarios || []).map((s) => s.id)),
    [envState],
  );
  const [picked, setPicked] = useState({});
  const isAdded = (c) => alreadyIn.has(replayScenarioId(c));
  const open = conversations.filter((c) => !isAdded(c));
  const chosen = open.filter((c) => picked[c.id]);

  const commit = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClose();
  };

  return (
    <SectionCard
      sx={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
      title={
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            component="span"
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            Production conversations, replayed word for word
          </Typography>
          <UiOnlyFlag feature="scenarioSources" />
        </Stack>
      }
      subtitle={SAMPLE_REPLAY_NOTE}
      action={
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
      }
    >
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
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
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
            channel={channel}
            added={isAdded(c)}
            picked={!!picked[c.id]}
            onToggle={() => setPicked((p) => ({ ...p, [c.id]: !p[c.id] }))}
          />
        ))}
      </Box>
    </SectionCard>
  );
}
ProductionRoute.propTypes = {
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  onClose: PropTypes.func.isRequired,
};

function ConversationRow({ conv, channel, added, picked, onToggle }) {
  const [open, setOpen] = useState(false);
  const toggleOpen = () => setOpen((o) => !o);
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggleOpen}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleOpen();
          }
        }}
        sx={{
          px: 1.5,
          py: 1,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: -2,
          },
        }}
      >
        <Box
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <Checkbox
            size="small"
            checked={picked || added}
            disabled={added}
            onChange={onToggle}
            inputProps={{ "aria-label": `Select ${conv.topic}` }}
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
              sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
            >
              {`${channel} · ${fmtDuration(conv.durationS)} · ${conv.when} · ${conv.turns.length} caller turns${conv.outcome === "failed" ? " · failed in production" : ""}`}
            </Typography>
            <PersonalDataTag row={conv} />
            {added && (
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                · already added
              </Typography>
            )}
          </Stack>
        </Box>
        <CustomTooltip
          show
          arrow
          size="small"
          title={open ? "Hide the caller's turns" : "Show the caller's turns"}
        >
          <IconButton
            size="small"
            tabIndex={-1}
            aria-label={
              open ? "Hide the caller's turns" : "Show the caller's turns"
            }
          >
            <Iconify
              icon={
                open
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.subtitle" }}
            />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={0.5}
          sx={{ px: 6, py: 1.5, bgcolor: "background.neutral" }}
        >
          {conv.turns.map((text, i) => (
            <Stack
              key={text}
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
                {`Caller ${i + 1}`}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {text}
              </Typography>
            </Stack>
          ))}
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", pt: 0.5 }}
          >
            {`Persona inferred: ${conv.persona.name} · ${conv.persona.archetype}. The agent's side isn't replayed. Your agent answers for itself.`}
          </Typography>
        </Stack>
      </Collapse>
    </Box>
  );
}
ConversationRow.propTypes = {
  conv: PropTypes.object.isRequired,
  channel: PropTypes.string,
  added: PropTypes.bool,
  picked: PropTypes.bool,
  onToggle: PropTypes.func,
};

function PersonalDataTag({ row }) {
  const line = personalDataLine(row);
  if (!line) return null;
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      title={personalDataDetail(row) || ""}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 20,
          borderRadius: "4px",
          cursor: "help",
          flexShrink: 0,
          color: "text.secondary",
          bgcolor: (t) => alpha(t.palette.text.primary, 0.06),
        }}
      >
        <Iconify icon="solar:shield-user-linear" width={12} />
        <Typography noWrap sx={{ typography: "s3" }}>
          {line}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}
PersonalDataTag.propTypes = { row: PropTypes.object };
