// A2A agent card run (options-truth L1 G5 C2): enter the agent card URL
// (or a base URL and Discover `/.well-known/agent-card.json`), see the
// agent's skills, pick one, map the message parts from columns, test one
// row with `message/send`, then run: one A2A task per row and try. Every
// row is its own task (no context id: continuity is not modelled).
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  MenuItem,
  Radio,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import { useCardValue } from "./cardWatch-g2";
import { PROMPT_CONFIG_TYPE } from "./constants";
import { newRunCard } from "./experimentFormValues";
import { a2aRunName, isAutoA2aName } from "./endpointCards-g2";
import {
  A2aAuthField,
  A2aTimeline,
} from "src/sections/common/RunSources/endpoint/httpCardParts";
import StandInTag from "src/sections/common/RunSources/endpoint/StandInTag";

const AGENT_CARD = "/model-hub/r4/a2a/agent-card/";
const MESSAGE_SEND = "/model-hub/r4/a2a/message/send/";

const resultOf = (res) => res?.data?.result ?? null;
// The app's axios rejects with the response body spread (`e.result`).
const errorOf = (e) =>
  e?.result ?? e?.response?.data?.result ?? e?.message ?? "The request failed";

/** The input column a message text part defaults to (role input, text). */
export function defaultTextColumn(columns = []) {
  return (
    columns.find(
      (c) => c.role === "input" && /text|string/.test(c.data_type ?? "text"),
    ) ??
    columns.find((c) => c.role === "input") ??
    columns[0] ??
    null
  )?.id;
}

const sel = { bgcolor: "background.paper", minWidth: 0 };

// The host an agent card URL names (`help.globex.test`), else the text.
const hostOf = (url) => {
  try {
    return new URL(String(url ?? "")).host || String(url ?? "");
  } catch {
    return String(url ?? "");
  }
};

export default function A2aRunForm({
  control,
  setValue,
  fieldPrefix,
  letters = [],
  onRemove,
  showErrors = false,
}) {
  const { datasetId, mappingColumns, sampleRow } = useExperimentForm();
  const name = `${fieldPrefix}.a2a`;
  const a2a = useCardValue(control, fieldPrefix, "a2a") ?? {};
  const columns = useMemo(() => mappingColumns ?? [], [mappingColumns]);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [test, setTest] = useState(null);
  const set = (patch) =>
    setValue(name, { ...a2a, ...patch }, { shouldDirty: true });
  const runName = useCardValue(control, fieldPrefix, "name");
  const wantName = a2aRunName(a2a.card ?? null, a2a.skill_id);
  useEffect(() => {
    if (runName !== wantName && isAutoA2aName(runName))
      setValue(`${fieldPrefix}.name`, wantName, { shouldDirty: true });
  }, [runName, wantName, fieldPrefix, setValue]);

  const discover = async () => {
    setBusy("discover");
    setErr(null);
    setTest(null);
    try {
      const r = resultOf(
        await axios.get(AGENT_CARD, { params: { url: a2a.card_url } }),
      );
      const card = r?.card ?? null;
      set({
        card,
        fetched_from: r?.fetched_from ?? null,
        skill_id: card?.skills?.some((s) => s.id === a2a.skill_id)
          ? a2a.skill_id
          : card?.skills?.[0]?.id ?? null,
        text_column: a2a.text_column ?? defaultTextColumn(columns),
      });
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setBusy("test");
    setErr(null);
    setTest(null);
    try {
      const r = resultOf(
        await axios.post(MESSAGE_SEND, {
          card_url: a2a.card_url,
          skill_id: a2a.skill_id,
          text_column: a2a.text_column,
          data_columns: a2a.data_columns ?? [],
          auth: a2a.auth ?? null,
          dataset_id: datasetId,
          row_id: sampleRow?.row_id ?? sampleRow?.id ?? null,
        }),
      );
      setTest(r);
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  const card = a2a.card ?? null;
  const missing = showErrors && (!card || !a2a.skill_id);
  return (
    <RunCardShell
      icon={
        <Iconify
          icon="mdi:card-account-details-outline"
          width={16}
          sx={{ color: "text.secondary" }}
        />
      }
      letters={letters}
      title={a2aRunName(card, a2a.skill_id)}
      typeLabel="One A2A task per row"
      onRemove={onRemove}
      error={missing ? "Discover the agent card and pick a skill" : null}
    >
      <Stack spacing={1.25} data-testid="a2a-form">
        <Stack direction="row" gap={1} alignItems="flex-start">
          <TextField
            size="small"
            fullWidth
            label="Agent card URL or base URL"
            placeholder="https://support.acme.test"
            value={a2a.card_url ?? ""}
            onChange={(e) => {
              // L7c g2 (knob:a2a:card-url-discover): a new URL drops the
              // card discovered at the old one; Discover it again.
              setTest(null);
              set(
                a2a.card
                  ? { card_url: e.target.value, card: null, fetched_from: null }
                  : { card_url: e.target.value },
              );
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                discover();
              }
            }}
            inputProps={{ "data-testid": "a2a-url" }}
            sx={sel}
          />
          <Button
            variant="outlined"
            size="small"
            onClick={discover}
            disabled={!String(a2a.card_url ?? "").trim() || busy === "discover"}
            data-testid="a2a-discover"
            sx={{ height: 40, flexShrink: 0 }}
          >
            {busy === "discover" ? <CircularProgress size={14} /> : "Discover"}
          </Button>
        </Stack>
        {!card && a2a.skill_id && String(a2a.card_url ?? "").trim() ? (
          <Typography
            typography="s2"
            color="warning.dark"
            data-testid="a2a-rediscover"
          >
            The URL changed: Discover the agent card again to run it.
          </Typography>
        ) : null}
        {card ? (
          <Box
            data-testid="a2a-card"
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              p: 1,
            }}
          >
            <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
              <Typography typography="s1" fontWeight="fontWeightSemiBold">
                {`${card.name} ${card.version}`}
              </Typography>
              <Typography typography="s3" color="text.secondary">
                {`${card.provider?.organization ?? ""} · ${a2a.fetched_from ?? card.url}`}
              </Typography>
              {card.capabilities?.streaming ? (
                <Chip size="small" label="Streaming" variant="outlined" />
              ) : null}
              {card.capabilities?.pushNotifications ? (
                <Chip
                  size="small"
                  label="Push notifications"
                  variant="outlined"
                />
              ) : null}
            </Stack>
            <Typography typography="s2" color="text.secondary" sx={{ mt: 0.5 }}>
              {card.description}
            </Typography>
            <Typography
              typography="s2"
              fontWeight="fontWeightMedium"
              sx={{ mt: 1 }}
            >
              {`Skills · ${card.skills.length}`}
            </Typography>
            <Stack spacing={0.5} sx={{ mt: 0.5 }}>
              {card.skills.map((s) => (
                <Stack
                  key={s.id}
                  direction="row"
                  gap={0.75}
                  alignItems="flex-start"
                  role="button"
                  tabIndex={0}
                  onClick={() => set({ skill_id: s.id })}
                  data-testid={`a2a-skill-${s.id}`}
                  sx={{
                    cursor: "pointer",
                    p: 0.5,
                    borderRadius: 0.5,
                    bgcolor:
                      a2a.skill_id === s.id ? "purple.o5" : "transparent",
                  }}
                >
                  <Radio
                    size="small"
                    checked={a2a.skill_id === s.id}
                    sx={{ p: 0.25 }}
                  />
                  <Box sx={{ minWidth: 0 }}>
                    <Typography typography="s2" fontWeight="fontWeightMedium">
                      {`${s.name} · ${s.id}`}
                    </Typography>
                    <Typography typography="s3" color="text.secondary">
                      {`${s.description} · ${s.tags.join(", ")} · in ${s.inputModes.join(", ")}`}
                    </Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>
          </Box>
        ) : null}
        {card ? (
          <Stack spacing={1} data-testid="a2a-map">
            <Typography typography="s2" fontWeight="fontWeightMedium">
              Map each row to the message
            </Typography>
            <Stack direction="row" gap={1}>
              <TextField
                select
                size="small"
                label="Text part from"
                value={a2a.text_column ?? ""}
                onChange={(e) => set({ text_column: e.target.value })}
                sx={{ ...sel, flex: 1 }}
                SelectProps={{ MenuProps: { disablePortal: false } }}
                inputProps={{ "data-testid": "a2a-text-column" }}
              >
                {columns.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.name}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                select
                size="small"
                label="Data part (JSON) from"
                value={a2a.data_columns ?? []}
                onChange={(e) =>
                  set({
                    data_columns: [].concat(e.target.value).filter(Boolean),
                  })
                }
                SelectProps={{ multiple: true }}
                sx={{ ...sel, flex: 1 }}
              >
                {columns.map((c) => (
                  <MenuItem key={c.id} value={c.id}>
                    {c.name}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
            <A2aAuthField auth={a2a.auth} onChange={(auth) => set({ auth })} />
            <Stack direction="row" gap={1} alignItems="center">
              <Button
                size="small"
                variant="outlined"
                onClick={runTest}
                disabled={!a2a.skill_id || busy === "test"}
                data-testid="a2a-test"
                startIcon={<Iconify icon="mdi:play-outline" width={14} />}
              >
                Test on one row
              </Button>
              <Typography typography="s3" color="text.secondary">
                {`Sends ${card?.capabilities?.streaming ? "message/stream" : "message/send"} for the sample row.`}
              </Typography>
            </Stack>
            {test ? (
              <Box
                data-testid="a2a-test-result"
                sx={{ bgcolor: "background.neutral", borderRadius: 1, p: 1 }}
              >
                <Stack
                  direction="row"
                  gap={1}
                  alignItems="center"
                  flexWrap="wrap"
                >
                  <Typography typography="s3" color="text.secondary">
                    {`Row ${test.row_no ?? 1} · ${test.task.taskId} · ${test.task.states.map((s) => s.state).join(" → ")} · ${test.task.latency_ms} ms`}
                  </Typography>
                  <StandInTag
                    who={hostOf(card?.url ?? a2a.card_url)}
                    testId="a2a-test-simulated"
                  />
                </Stack>
                <Typography
                  typography="s2"
                  sx={{ mt: 0.5, whiteSpace: "pre-wrap" }}
                >
                  {test.task.artifact?.text ?? test.task.error}
                </Typography>
                <A2aTimeline
                  timeline={test.timeline}
                  headers={test.request?.headers}
                />
              </Box>
            ) : null}
          </Stack>
        ) : null}
        {err ? (
          <Typography typography="s2" color="error.main">
            {err}
          </Typography>
        ) : null}
        <Link
          component="button"
          type="button"
          typography="s3"
          underline="hover"
          onClick={() =>
            setValue(fieldPrefix, newRunCard(PROMPT_CONFIG_TYPE.HTTP), {
              shouldDirty: true,
            })
          }
          data-testid="a2a-use-http"
          sx={{ alignSelf: "flex-start" }}
        >
          Your agent speaks plain HTTP? Use an HTTP endpoint
        </Link>
      </Stack>
    </RunCardShell>
  );
}

A2aRunForm.propTypes = {
  control: PropTypes.object.isRequired,
  setValue: PropTypes.func.isRequired,
  fieldPrefix: PropTypes.string.isRequired,
  letters: PropTypes.arrayOf(PropTypes.string),
  onRemove: PropTypes.func,
  showErrors: PropTypes.bool,
};
