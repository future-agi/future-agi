// A2A agent card run (options-truth L1 G5 C2): enter the agent card URL
// (or a base URL and Discover `/.well-known/agent-card.json`), see the
// agent's skills, pick one, map the message text from a column, test one
// row with `message/send`, then run: one `message/send` per row. Every row
// is its own task (no context id: continuity is not modelled).
// Discover and Test go through the core's endpoint test route, which calls
// public hosts only and resolves the auth secret in the workspace. The run
// itself is saved as an HTTP source with protocol "a2a" (a2aMessage.js).
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
import {
  ENDPOINT_TEST,
  a2aAuthProblem,
  a2aMessageBody,
  a2aReplyOf,
  a2aRequestHeaders,
  agentCardUrls,
  agentJsonRpcUrl,
  isAgentCard,
  normalizedCard,
} from "src/sections/common/RunSources/endpoint/a2aMessage";
import { valuesById } from "src/sections/common/RunSources/endpoint/requestModel";

const resultOf = (res) => res?.data?.result ?? null;
const ok = (status) => status >= 200 && status < 300;
// The app's axios rejects with the response body spread (`e.result`). Only
// the core's own sentence is shown; anything else gets a plain line.
const errorOf = (e) => {
  const v = e?.result ?? e?.response?.data?.result;
  return typeof v === "string" && v.trim()
    ? v
    : "The request didn't go through. Check the agent URL and auth, then try again.";
};

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
      const urls = agentCardUrls(a2a.card_url);
      if (!urls.length) {
        setErr(
          "Enter the agent's URL with https://, for example https://support.acme.test.",
        );
        return;
      }
      let found = null;
      let problem = null;
      for (const url of urls) {
        const r = resultOf(
          await axios.post(ENDPOINT_TEST, {
            prepared_request: { url, method: "GET", headers: [], body: "" },
          }),
        );
        if (ok(r?.status) && r?.response_is_json && isAgentCard(r.response)) {
          found = { card: normalizedCard(r.response), fetched_from: url };
          break;
        }
        // A host we cannot reach or call answers the same for every path.
        if (r?.status == null && r?.error) {
          problem = r.error;
          break;
        }
      }
      if (!found) {
        setErr(
          problem ??
            `No agent card at ${urls.join(" or ")}. Enter the agent card URL, or a base URL that serves /.well-known/agent-card.json.`,
        );
        return;
      }
      const { card } = found;
      set({
        card,
        fetched_from: found.fetched_from,
        skill_id: card.skills.some((s) => s.id === a2a.skill_id)
          ? a2a.skill_id
          : card.skills[0]?.id ?? null,
        text_column: a2a.text_column ?? defaultTextColumn(columns),
      });
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setErr(null);
    setTest(null);
    const url = agentJsonRpcUrl(a2a.card);
    const headers = a2aRequestHeaders(a2a.auth);
    if (!url) {
      setErr(
        "This agent card names no JSON-RPC URL. Future AGI sends A2A message/send over JSON-RPC: use an agent that offers it.",
      );
      return;
    }
    if (!headers) {
      setErr(a2aAuthProblem(a2a.auth));
      return;
    }
    if (!a2a.text_column) {
      setErr("Pick the column the message text comes from, then test again.");
      return;
    }
    setBusy("test");
    const rowId = sampleRow?.row_id ?? sampleRow?.id ?? null;
    try {
      const text = valuesById(sampleRow, columns)[a2a.text_column];
      const r = resultOf(
        await axios.post(ENDPOINT_TEST, {
          dataset_id: datasetId ?? null,
          row_id: rowId,
          prepared_request: {
            url,
            method: "POST",
            headers,
            body: a2aMessageBody(text, rowId),
          },
        }),
      );
      const reply = r?.response_is_json
        ? a2aReplyOf(r.response)
        : { text: null, error: null, state: null, taskId: null };
      const error =
        r?.error ??
        reply.error ??
        (r?.response_is_json
          ? null
          : "The agent's reply is not JSON. Check the agent URL, then test again.");
      const latency = Math.round(r?.latency_ms ?? 0);
      setTest({
        simulated: r?.simulated === true,
        row_no: 1,
        task: {
          taskId: reply.taskId,
          states: reply.state ? [{ state: reply.state }] : [],
          latency_ms: latency,
          artifact: error ? null : { text: reply.text },
          error,
        },
        timeline: [
          {
            name: "message/send",
            method: "message/send",
            state: error ? "failed" : reply.state ?? "answered",
            http_status: r?.status ?? null,
            latency_ms: latency,
          },
        ],
        request: r?.request ?? null,
      });
    } catch (e) {
      setErr(errorOf(e));
    } finally {
      setBusy(null);
    }
  };

  // A card saved before Discover filled every field still shows (no crash).
  const card = a2a.card ? normalizedCard(a2a.card) : null;
  const missing = showErrors && (!card || !a2a.skill_id);
  // Where each row's message (and the auth secret) goes: the card names it,
  // and it can be another host than the one the card was read from.
  const sendHost = card ? hostOf(agentJsonRpcUrl(card) ?? "") : "";
  const cardHost = a2a.fetched_from ? hostOf(a2a.fetched_from) : "";
  const sendsSecret = (a2aRequestHeaders(a2a.auth) ?? []).length > 0;
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
            {sendHost ? (
              <Typography
                typography="s3"
                color="text.secondary"
                data-testid="a2a-send-host"
              >
                {`${sendsSecret ? "Each row's message and the auth secret go to" : "Each row's message goes to"} ${sendHost}.${cardHost && cardHost !== sendHost ? ` The card was read from ${cardHost}.` : ""}`}
              </Typography>
            ) : null}
            <Stack direction="row" gap={1} alignItems="center">
              <Button
                size="small"
                variant="outlined"
                onClick={runTest}
                disabled={!a2a.skill_id || busy === "test"}
                data-testid="a2a-test"
              >
                Test on one row
              </Button>
              <Typography typography="s3" color="text.secondary">
                Sends message/send for the sample row.
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
                    {[
                      `Row ${test.row_no ?? 1}`,
                      test.task.taskId,
                      test.task.states.map((s) => s.state).join(" → "),
                      `${test.task.latency_ms} ms`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </Typography>
                  {/* Only a simulated answer says so; a real agent's does not. */}
                  {test.simulated ? (
                    <StandInTag
                      who={hostOf(card?.url ?? a2a.card_url)}
                      testId="a2a-test-simulated"
                    />
                  ) : null}
                </Stack>
                <Typography
                  typography="s2"
                  color={test.task.error ? "error.main" : undefined}
                  sx={{ mt: 0.5, whiteSpace: "pre-wrap" }}
                  data-testid="a2a-test-reply"
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
          <Typography
            typography="s2"
            color="error.main"
            role="alert"
            data-testid="a2a-error"
          >
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
