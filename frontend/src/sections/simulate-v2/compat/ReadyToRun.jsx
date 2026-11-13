import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { StatusDot } from "../components/primitives";
import { readinessOf } from "../_mock/readiness";
import { PAIR_CHECK_LIMIT_MIN } from "../_mock/compatibility";
import { ago } from "../agents/agentViewUtils";
import { fieldFixOf, fixKindOf, fixLabelOf, shortFixLabel } from "./fixKinds";
import BuilderFixChat, { builderPromptFor } from "./BuilderFixChat";

/*
  Ready to run (plan T06; runs.md step 1): the short panel in Run setup for
  this agent version on this environment. Not a report, no score, no cost.

  14 Nov information flow (Chintan: "figure out the information flow"): the
  run plan's verdict line says whether the run can start; this panel only
  says what holds scenarios back. One line per problem with one action;
  what the check saw opens on demand. Rows still to check fold into one
  line; ready areas into another. Known limits of where the agent runs sit
  under How it runs (KnownLimits below), not here.
*/
const namesOf = (ids, titleOf) => {
  /* Two scenarios can share a title; name each title once. */
  const names = [...new Set(ids.map(titleOf).filter(Boolean))].map((t) =>
    t.replace(/\.$/, ""),
  );
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
};
const firstSentence = (d) =>
  String(d || "")
    .split(/\s+—\s+|(?<=\.)\s/)[0]
    .replace(/\.$/, "");
const lower = (s) => String(s || "").toLowerCase();
const BUILDER = ["tools", "release", "rebuild"];
/* A stated limit of where the agent runs: nothing to fix. */
const isLimit = (a) =>
  a.status === "partial" &&
  ["tools", "reset", "cancel", "evidence"].includes(a.id);
/* What changed since a stale row was checked, in a few words. */
const staleReasonOf = (a) => {
  const d = String(a.staleRow?.detail || "");
  if (/^Fix applied/.test(d)) return "a fix was applied, not re-checked yet";
  return d
    .replace(/^Was Supported\.\s*/, "")
    .replace(/\s+—\s+re-check.*$/, "")
    .replace(/\.$/, "");
};
/* Short names for the areas, as the one-line summaries use them. */
const SHORT = {
  start: "start",
  tools: "tools",
  reset: "reset",
  cancel: "stop",
  evidence: "evidence",
  grading: "grading",
};
const shortOf = (a) => SHORT[a.id] || lower(a.label);

/* An opened problem: what the check saw, the fix, what changes once fixed,
   and the scenarios it blocks. */
function Facts({ rows }) {
  const shown = rows.filter(([, v]) => v);
  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: "88px minmax(0, 1fr)",
        columnGap: 1.5,
        rowGap: 0.5,
      }}
    >
      {shown.map(([k, v]) => (
        <Box key={k} sx={{ display: "contents" }}>
          <Typography
            component="dt"
            sx={{ typography: "s3", color: "text.tertiary", pt: "1px" }}
          >
            {k}
          </Typography>
          <Typography component="dd" sx={{ typography: "s2", m: 0 }}>
            {v}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
Facts.propTypes = { rows: PropTypes.array.isRequired };

/* A quiet one-line summary row (ready, waiting, not checked). */
function Line({ dot, hollow = false, children, tip = "" }) {
  return (
    <Tooltip arrow placement="top-start" title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 1.5,
          py: 0.875,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <StatusDot status={dot} size={6} hollow={hollow} />
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
        >
          {children}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
Line.propTypes = {
  dot: PropTypes.string,
  hollow: PropTypes.bool,
  children: PropTypes.node,
  tip: PropTypes.string,
};

/* Known limits of where the agent runs (14 Nov: they change how results
   read, not whether the run starts, so they sit under How it runs). */
export function KnownLimits({ report, scenarios, evalCount, provider }) {
  const limitBy = provider
    ? provider.charAt(0).toUpperCase() + provider.slice(1)
    : "Hosting";
  const limits = readinessOf(report, scenarios, evalCount).filter(isLimit);
  const voiceReplies = (report.rows || []).find(
    (r) => r.id === "message" && r.status === "unavailable",
  );
  const n = limits.length + (voiceReplies ? 1 : 0);
  if (!n) return null;
  const phrase = (a) => {
    const d = String(a.detail || "");
    if (a.id === "tools" && /^Tools run in your/.test(d))
      return `tools act in your ${limitBy} account`;
    if (a.id === "reset") return "the agent may remember earlier attempts";
    if (a.id === "cancel")
      return /nothing ends the/.test(d)
        ? `a stop doesn't end the ${limitBy} call yet`
        : "a stop may not end work already sent";
    return lower(firstSentence(d));
  };
  return (
    <Tooltip
      arrow
      placement="top-start"
      title={
        <Box sx={{ whiteSpace: "pre-line" }}>
          {[
            ...limits.map((a) => `${a.label}: ${phrase(a)}`),
            ...(voiceReplies
              ? [`${voiceReplies.label}: ${voiceReplies.detail}`]
              : []),
            "These come from where the agent runs, not your setup, so there's nothing to fix. Each run's results carry the same note.",
          ].join("\n\n")}
        </Box>
      }
    >
      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {`${limitBy === "Hosting" ? "Runs outside our sandbox" : `Runs on ${limitBy}`} · ${n} known limit${n === 1 ? "" : "s"}`}
      </Typography>
    </Tooltip>
  );
}
KnownLimits.propTypes = {
  report: PropTypes.object.isRequired,
  scenarios: PropTypes.array.isRequired,
  evalCount: PropTypes.number,
  provider: PropTypes.string,
};

export default function ReadyToRun({
  report,
  scenarios,
  evalCount,
  onFix,
  onCheck,
  checking = false,
  /* What a fix made here needs (connection spec, applyRepair(s), release
     checks, rebuild); without it every fix opens the agent. */
  fixKit = null,
  /* Builder fixes go to the environment's builder chat (14 Nov); without
     it they run in the small chat inside this panel. */
  onFixInBuilder = null,
}) {
  const [openId, setOpenId] = useState(null);
  const [values, setValues] = useState({});
  const [chatJobs, setChatJobs] = useState(null);
  const [fixed, setFixed] = useState([]);
  const toggle = (id) => setOpenId((cur) => (cur === id ? null : id));

  const areas = readinessOf(report, scenarios, evalCount);
  const titleOf = (id) => scenarios.find((s) => s.id === id)?.title || null;
  const blockedIds = new Set((report.scope?.blocked || []).map((b) => b.id));
  const blockedOf = (a) => a.affected.filter((id) => blockedIds.has(id));
  const kindOf = (a) =>
    a.fixRow ? fixKindOf(a.fixRow, fixKit) || "link" : null;
  const blockers = areas.filter(
    (a) => a.status === "blocked" && !fixed.some((f) => f.id === a.id),
  );
  const unchecked = areas.filter((a) => a.status === "unchecked");
  const waiting = unchecked.filter((a) => a.waits);
  const stale = unchecked.filter((a) => !a.waits && a.staleRow);
  const notYet = unchecked.filter((a) => !a.waits && !a.staleRow);
  const ready = areas.filter((a) => a.status === "ready" || isLimit(a));
  const val = report.validation;

  /* Builder fixes: one row at a time, handed to the builder chat. */
  const startBuilder = (a) => {
    if (chatJobs) return;
    setOpenId(null);
    const job = { id: a.id, label: a.label, kind: kindOf(a), row: a.fixRow };
    if (onFixInBuilder) {
      onFixInBuilder([job]);
      return;
    }
    setChatJobs([job]);
  };
  const builderDone = () => {
    const jobs = chatJobs || [];
    const repairs = jobs
      .filter((j) => j.kind === "tools")
      .map((j) => ({ fixId: j.row.fixId }));
    if (repairs.length) fixKit?.applyRepairs?.(repairs);
    if (jobs.some((j) => j.kind === "release")) fixKit?.runReleaseChecks?.();
    if (jobs.some((j) => j.kind === "rebuild")) fixKit?.rebuild?.();
    setFixed((f) => [...f, ...jobs.map((j) => ({ id: j.id, label: j.label }))]);
  };

  /* Everything ready means the pair was checked, even where a platform
     check keeps no date. */
  const checked = !!val || (!blockers.length && !unchecked.length);
  const checkButton = onCheck && (
    <Button
      size="small"
      variant="outlined"
      disabled={checking}
      onClick={onCheck}
      sx={{ py: 0, flexShrink: 0 }}
    >
      {checking ? "Checking…" : checked ? "Re-check" : "Run the check"}
    </Button>
  );

  /* Nothing to do: the panel is one line. */
  if (!blockers.length && !unchecked.length && !chatJobs && !fixed.length)
    return (
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{
          px: 1.5,
          minHeight: 40,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        <StatusDot status="passed" size={6} />
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 500 }}>
            Ready to run
          </Box>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {` · ${ready.map(shortOf).join(", ")}${val?.at ? ` · checked ${ago(val.at)}` : ""}`}
          </Box>
        </Typography>
        {checkButton}
      </Stack>
    );

  const actionOf = (a) => {
    const kind = kindOf(a);
    const off = checking || !!chatJobs;
    if (kind === "field")
      return (
        <Button
          size="small"
          variant="outlined"
          disabled={off}
          onClick={() => toggle(a.id)}
          sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Add value
        </Button>
      );
    if (BUILDER.includes(kind))
      return (
        <Button
          size="small"
          variant="outlined"
          disabled={off}
          onClick={() => startBuilder(a)}
          sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Fix with builder chat
        </Button>
      );
    if (kind === "link" && onFix)
      return (
        <Button
          size="small"
          variant="outlined"
          onClick={() => onFix(a.fixRow)}
          sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        >
          Open the agent
        </Button>
      );
    return null;
  };

  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, minHeight: 40 }}
      >
        <Typography sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ fontWeight: 500 }}>
            Ready to run
          </Box>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {blockers.length
              ? ` · ${blockers.length} to fix`
              : unchecked.length
                ? " · not checked"
                : ""}
          </Box>
        </Typography>
        {checkButton}
      </Stack>

      {/* One line per problem; its detail opens on demand. */}
      {blockers.map((a) => {
        const r = a.fixRow || {};
        const kind = kindOf(a);
        const open = openId === a.id;
        const n = blockedOf(a).length;
        const field = kind === "field" ? fieldFixOf(r, fixKit) : null;
        const value = values[a.id] ?? field?.value ?? "";
        const said = String(r.evidence || "").replace(/^[^:]+:\s*/, "");
        const detail = r.detail || a.detail;
        const saw =
          said && !String(detail).includes(firstSentence(said))
            ? `${detail} ${said}`
            : detail;
        return (
          <Box
            key={a.id}
            sx={{
              borderTop: "1px solid",
              borderColor: "divider",
              ...(open && { bgcolor: "background.neutral" }),
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{ px: 1.5, py: 1 }}
            >
              <StatusDot status="failed" size={6} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography sx={{ typography: "s2", fontWeight: 500 }}>
                  {a.label}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {lower(firstSentence(detail))}
                  {n
                    ? ` · blocks ${n === report.scope?.requested && n > 1 ? `all ${n}` : n}`
                    : ""}
                  {" · "}
                  <Box
                    component="button"
                    type="button"
                    onClick={() => toggle(a.id)}
                    aria-expanded={open}
                    sx={{
                      p: 0,
                      border: 0,
                      bgcolor: "transparent",
                      cursor: "pointer",
                      typography: "s3",
                      color: "primary.main",
                    }}
                  >
                    {open ? "Hide details" : "Details"}
                  </Box>
                </Typography>
              </Box>
              {actionOf(a)}
            </Stack>
            {open && (
              <Stack spacing={1.25} sx={{ px: 1.5, pb: 1.5, pl: 3.75 }}>
                <Facts
                  rows={[
                    ["What we saw", saw],
                    ["Fix", shortFixLabel(fixLabelOf(r))],
                    ["Once fixed", r.fix?.would],
                    [
                      "Blocks",
                      n
                        ? `${n} scenario${n === 1 ? "" : "s"}: ${namesOf(blockedOf(a), titleOf)}`
                        : null,
                    ],
                    [
                      "Sends",
                      BUILDER.includes(kind)
                        ? `“${builderPromptFor(kind, r)}” to the builder chat`
                        : null,
                    ],
                  ]}
                />
                {field && (
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "6px",
                      bgcolor: "background.paper",
                    }}
                  >
                    <Stack
                      direction="row"
                      spacing={1.5}
                      alignItems="center"
                      sx={{ px: 1.25, py: 0.75 }}
                    >
                      <Typography
                        sx={{ typography: "s2", width: 120, flexShrink: 0 }}
                      >
                        {field.label}
                      </Typography>
                      <TextField
                        size="small"
                        value={value}
                        onChange={(e) =>
                          setValues((v) => ({ ...v, [a.id]: e.target.value }))
                        }
                        inputProps={{ "aria-label": field.label }}
                        sx={{
                          flex: 1,
                          "& input": { typography: "s2", py: 0.625 },
                        }}
                      />
                    </Stack>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        px: 1.25,
                        pb: 0.5,
                      }}
                    >
                      {r.suggestion?.why
                        ? `${r.suggestion.why} Only this field changes; saving checks the pair again.`
                        : "Only this field changes; saving checks the pair again."}
                    </Typography>
                    <Stack
                      direction="row"
                      spacing={1}
                      justifyContent="flex-end"
                      sx={{ px: 1.25, pb: 0.75 }}
                    >
                      <Button
                        size="small"
                        color="inherit"
                        onClick={() => setOpenId(null)}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={!String(value).trim() || checking}
                        onClick={() => {
                          fixKit.applyRepair(r.fixId, String(value));
                          setOpenId(null);
                        }}
                      >
                        Save and re-check
                      </Button>
                    </Stack>
                  </Box>
                )}
              </Stack>
            )}
          </Box>
        );
      })}

      {fixed.map((f) => (
        <Line key={`fixed-${f.id}`} dot="passed">
          {`${f.label} · fixed`}
        </Line>
      ))}
      {chatJobs && (
        <BuilderFixChat
          jobs={chatJobs}
          onDone={builderDone}
          onClose={() => setChatJobs(null)}
        />
      )}

      {/* Rows still to check fold into one line each. */}
      {/* Never checked: everything still to check is one line. */}
      {!val && waiting.length + notYet.length > 0 && (
        <Line dot="queued" hollow>
          {`${[...notYet, ...waiting].map(shortOf).join(", ")} get checked together · up to ${PAIR_CHECK_LIMIT_MIN} min`.replace(
            /^./,
            (c) => c.toUpperCase(),
          )}
        </Line>
      )}
      {/* Checked on another version: the verdict says why; one line says
         what to check again. */}
      {val && stale.length > 0 && (
        <Line dot="flaky" tip={staleReasonOf(stale[0])}>
          {`Check again on this version: ${[...stale, ...waiting, ...notYet].map(shortOf).join(", ")}`}
        </Line>
      )}
      {val && !stale.length && waiting.length > 0 && (
        <Line dot="queued" hollow>
          {`Waiting on ${lower(waiting[0].fixRow?.blockedBy || "starts and replies")}: ${waiting.map(shortOf).join(", ")}`}
        </Line>
      )}
      {val && !stale.length && notYet.length > 0 && (
        <Line dot="queued" hollow>
          {`Not checked yet: ${notYet.map(shortOf).join(", ")}`}
        </Line>
      )}
      {ready.length > 0 && (
        <Line dot="passed">{`Also ready: ${ready.map(shortOf).join(", ")}`}</Line>
      )}
    </Box>
  );
}
ReadyToRun.propTypes = {
  report: PropTypes.object.isRequired,
  scenarios: PropTypes.array.isRequired,
  evalCount: PropTypes.number,
  onFix: PropTypes.func,
  onCheck: PropTypes.func,
  checking: PropTypes.bool,
  fixKit: PropTypes.object,
  onFixInBuilder: PropTypes.func,
};
