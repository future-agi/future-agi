import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { StatusDot } from "../components/primitives";
import { READY_STATUS, readinessOf } from "../_mock/readiness";
import { PAIR_CHECK_LIMIT_MIN } from "../_mock/compatibility";
import { ago } from "../agents/agentViewUtils";
import { fieldFixOf, fixKindOf, fixLabelOf, shortFixLabel } from "./fixKinds";
import BuilderFixChat, { builderPromptFor } from "./BuilderFixChat";

/*
  Ready to run (plan T06; runs.md step 1): the short panel in Run setup for
  this agent version on this environment. Not a report, no score, no cost.

  Its rows are grouped by who can settle them (13 Nov, mixing a teammate's
  run plan the manager liked with ours): Needs you — a value or a change on
  the agent only the person can make; Builder chat can fix — environment-side
  fixes, ticked and sent to the builder in one go; Re-check to confirm — rows
  not checked yet, or checked on another version. One action per group, not
  a Fix on every row; each row opens to say what its fix does.
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
const BUILDER = ["tools", "release", "rebuild"];
/* What each area's check proves, said on a row that still needs it. */
const CHECKS = {
  start: "The agent starts, answers a message and keeps the conversation.",
  tools: "Every tool the scenarios need exists and lands in the test world.",
  reset: "Each trial starts from a fresh world.",
  cancel: "Stopping a run ends the agent's work.",
  evidence: "The agent leaves the records the checks grade.",
};

/* A group: its own box, so three groups on screen at once still read
   apart (14 Nov, Nosang: the grouping she expected). */
function Group({ children }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        overflow: "hidden",
        "& > :first-of-type": { borderTop: "none" },
      }}
    >
      {children}
    </Box>
  );
}
Group.propTypes = { children: PropTypes.node };

/* A group's header: who settles these, how many, and its one action. */
function GroupHead({ icon, title, sub, count, action, lead = null }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 0.75,
        borderTop: "1px solid",
        borderColor: "divider",
      }}
    >
      {lead || (
        <Iconify
          icon={icon}
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      )}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
          <Box
            component="span"
            sx={{ color: "text.tertiary", fontWeight: 400 }}
          >{` · ${count}`}</Box>
        </Typography>
        {sub && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {sub}
          </Typography>
        )}
      </Box>
      {action}
    </Stack>
  );
}
GroupHead.propTypes = {
  icon: PropTypes.string,
  title: PropTypes.string,
  sub: PropTypes.string,
  count: PropTypes.number,
  action: PropTypes.node,
  lead: PropTypes.node,
};

/* One row: its dot, area and reason in a line; it opens to its fix. */
function Row({ area, open, onToggle, lead = null, end = null, children }) {
  const st = READY_STATUS[area.status] || READY_STATUS.unchecked;
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => e.key === "Enter" && onToggle()}
        sx={{ px: 1.5, py: 0.625, cursor: "pointer" }}
      >
        {lead}
        <StatusDot
          status={st.dot}
          size={6}
          hollow={area.status === "unchecked"}
        />
        <Typography noWrap sx={{ typography: "s2", flexShrink: 0 }}>
          {area.label}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {area.status === "unchecked" && !area.detail
            ? "Not checked yet"
            : firstSentence(area.detail)}
        </Typography>
        {end}
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={14}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />
      </Stack>
      {open && (
        <Stack spacing={1} sx={{ px: 1.5, pb: 1.25, pl: 4.5 }}>
          {children}
        </Stack>
      )}
    </Box>
  );
}
Row.propTypes = {
  area: PropTypes.object.isRequired,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  lead: PropTypes.node,
  end: PropTypes.node,
  children: PropTypes.node,
};

/* An opened row, as a short spec sheet: what the check saw, the fix, what
   changes once it's made, and which scenarios wait on it (14 Nov, Nosang:
   a one-line summary didn't show the real case). */
function Facts({ rows }) {
  const shown = rows.filter(([, v]) => v);
  return (
    <Box
      component="dl"
      sx={{
        m: 0,
        display: "grid",
        gridTemplateColumns: "96px minmax(0, 1fr)",
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
  children,
}) {
  const [openId, setOpenId] = useState(null);
  const [values, setValues] = useState({});
  const [unticked, setUnticked] = useState([]);
  const [chatJobs, setChatJobs] = useState(null);
  const [fixed, setFixed] = useState([]);
  const toggle = (id) => setOpenId((cur) => (cur === id ? null : id));

  /* Who a stated limit comes from: the platform the agent runs on, or
     wherever it's hosted. */
  const provider = fixKit?.agent?.values?.provider;
  const limitBy = provider
    ? provider.charAt(0).toUpperCase() + provider.slice(1)
    : "Hosting";
  const areas = readinessOf(report, scenarios, evalCount);
  const titleOf = (id) => scenarios.find((s) => s.id === id)?.title || null;
  const blockedIds = new Set((report.scope?.blocked || []).map((b) => b.id));
  const blockedOf = (a) => a.affected.filter((id) => blockedIds.has(id));

  /* A stated limit of where the agent runs (Partial on tools, reset, stop or
     evidence) has nothing to fix: one quiet line, so results are read right. */
  const isLimit = (a) =>
    a.status === "partial" &&
    ["tools", "reset", "cancel", "evidence"].includes(a.id);
  const limits = areas.filter(isLimit);
  const voiceReplies = (report.rows || []).find(
    (r) => r.id === "message" && r.status === "unavailable",
  );
  /* A row keeps its group while a check runs; only its actions wait. */
  const kindOf = (a) =>
    a.fixRow ? fixKindOf(a.fixRow, fixKit) || "link" : null;
  const blockers = areas.filter((a) => a.status === "blocked");
  const fixing = new Set((chatJobs || []).map((j) => j.id));
  const builderRows = blockers.filter(
    (a) => BUILDER.includes(kindOf(a)) && !fixed.some((f) => f.id === a.id),
  );
  const youRows = blockers.filter((a) => !builderRows.includes(a));
  const recheckRows = areas.filter((a) => a.status === "unchecked");
  const ready = areas.filter((a) => a.status === "ready" || isLimit(a));
  const ticked = builderRows.filter((a) => !unticked.includes(a.id));
  const picked = ticked.filter((a) => !fixing.has(a.id));

  const limitPhrase = (a) => {
    const d = String(a.detail || "");
    if (a.id === "tools" && /^Tools run in your/.test(d))
      return `tools act in your ${limitBy} account`;
    if (a.id === "reset") return "the agent may remember earlier attempts";
    if (a.id === "cancel")
      return /nothing ends the/.test(d)
        ? `a stop doesn't end the ${limitBy} call yet`
        : "a stop may not end work already sent";
    return firstSentence(d).replace(/^./, (c) => c.toLowerCase());
  };

  /* The pairing's check, said once above the groups. */
  const pair = `agent ${report.identity?.agentVersion || "v1"} on environment ${report.identity?.worldVersion || "v1"}`;
  const val = report.validation;
  const state = checking ? "validating" : val?.state;
  const pairLine =
    state === "validating"
      ? {
          dot: "running",
          text: `Checking ${pair} · up to ${PAIR_CHECK_LIMIT_MIN} min`,
        }
      : state === "failed"
        ? {
            dot: "failed",
            text: `Last check failed ${ago(val.at)}: ${val.reason}`,
          }
        : state === "timed_out"
          ? {
              dot: "failed",
              text: `Last check timed out ${ago(val.at)} (over ${PAIR_CHECK_LIMIT_MIN} min)`,
            }
          : state === "unavailable"
            ? {
                dot: "cancelled",
                text: `Check unavailable ${ago(val.at)} on our side · runs aren't held`,
              }
            : state === "stale"
              ? { dot: "needs_input", text: `${report.staleWhy} · re-check` }
              : null;

  /* Builder fixes: ticked rows, or one row, sent to the builder chat. */
  const startBuilder = (rows) => {
    if (!rows.length || chatJobs) return;
    setOpenId(null);
    setChatJobs(
      rows.map((a) => ({
        id: a.id,
        label: a.label,
        kind: kindOf(a),
        row: a.fixRow,
      })),
    );
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

  const blockedNote = (a) => {
    const n = blockedOf(a).length;
    return n ? (
      <Tooltip
        arrow
        title={`Blocked in this run: ${namesOf(blockedOf(a), titleOf)}`}
      >
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >{`${n} blocked`}</Typography>
      </Tooltip>
    ) : null;
  };
  /* What the check saw, the fix, what changes once it's made, and what
     waits on it. */
  const factsOf = (a) => {
    const r = a.fixRow || {};
    /* The check's evidence only when it adds to the detail. */
    const detail = r.detail || a.detail;
    const said = String(r.evidence || "").replace(/^[^:]+:\s*/, "");
    const saw =
      said && !String(detail).includes(firstSentence(said))
        ? `${detail} ${said}`
        : detail;
    const n = blockedOf(a).length;
    return (
      <Facts
        rows={[
          ["What we saw", saw],
          ["Fix", fixLabelOf(r)],
          ["Once fixed", r.fix?.would],
          [
            "Waits on it",
            n
              ? `${n} scenario${n === 1 ? "" : "s"}: ${namesOf(blockedOf(a), titleOf)}`
              : null,
          ],
        ]}
      />
    );
  };

  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Ready to run
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {/* Blockers only — how many scenarios run is said once, under the
              repeats (12 Nov review: two counts disagreed). */}
          {blockers.length
            ? `${blockers.length} blocker${blockers.length === 1 ? "" : "s"} to fix`
            : "No blocker"}
        </Typography>
        {(val || checking) && onCheck && !recheckRows.length && (
          <Button
            size="small"
            variant="outlined"
            disabled={checking}
            onClick={onCheck}
            sx={{ py: 0, flexShrink: 0 }}
          >
            {checking ? "Checking…" : "Re-check"}
          </Button>
        )}
      </Stack>

      {pairLine && (
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{
            px: 1.5,
            py: 0.75,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ pt: "6px", flexShrink: 0, display: "flex" }}>
            <StatusDot
              status={pairLine.dot}
              size={6}
              live={state === "validating"}
            />
          </Box>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {pairLine.text}
          </Typography>
        </Stack>
      )}

      {(youRows.length > 0 ||
        builderRows.length > 0 ||
        fixed.length > 0 ||
        recheckRows.length > 0) && (
        <Stack
          spacing={1}
          sx={{ p: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          {/* ── Needs you: a value, or a change on the agent ── */}
          {youRows.length > 0 && (
            <Group>
              <GroupHead
                icon="solar:user-linear"
                title="Needs you"
                sub="Only you can change these"
                count={youRows.length}
              />
              {youRows.map((a) => {
                const kind = kindOf(a);
                const field =
                  kind === "field" ? fieldFixOf(a.fixRow, fixKit) : null;
                const value = values[a.id] ?? field?.value ?? "";
                return (
                  <Row
                    key={a.id}
                    area={a}
                    open={openId === a.id}
                    onToggle={() => toggle(a.id)}
                    end={blockedNote(a)}
                  >
                    {factsOf(a)}
                    {field && (
                      <Box
                        sx={{
                          border: "1px solid",
                          borderColor: "divider",
                          borderRadius: "6px",
                        }}
                      >
                        <Stack
                          direction="row"
                          spacing={1.5}
                          sx={{
                            px: 1.25,
                            py: 0.5,
                            borderBottom: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          {["Field", "Value"].map((h, i) => (
                            <Typography
                              key={h}
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                width: i ? "auto" : 120,
                              }}
                            >
                              {h}
                            </Typography>
                          ))}
                        </Stack>
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
                              setValues((v) => ({
                                ...v,
                                [a.id]: e.target.value,
                              }))
                            }
                            inputProps={{ "aria-label": field.label }}
                            sx={{
                              flex: 1,
                              "& input": { typography: "s2", py: 0.625 },
                            }}
                          />
                        </Stack>
                        {a.fixRow?.suggestion?.why && (
                          <Typography
                            sx={{
                              typography: "s3",
                              color: "text.tertiary",
                              px: 1.25,
                              pb: 0.5,
                            }}
                          >
                            {a.fixRow.suggestion.why}
                          </Typography>
                        )}
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
                              fixKit.applyRepair(a.fixRow.fixId, String(value));
                              setOpenId(null);
                            }}
                          >
                            Save and re-check
                          </Button>
                        </Stack>
                      </Box>
                    )}
                    {kind === "link" && onFix && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => onFix(a.fixRow)}
                        endIcon={
                          <Iconify icon="solar:arrow-right-linear" width={12} />
                        }
                        sx={{
                          alignSelf: "flex-start",
                          whiteSpace: "normal",
                          textAlign: "left",
                        }}
                      >
                        {shortFixLabel(fixLabelOf(a.fixRow)) ||
                          "Open the connection"}
                      </Button>
                    )}
                  </Row>
                );
              })}
            </Group>
          )}

          {/* ── Builder chat can fix: environment-side, ticked and sent at once ── */}
          {(builderRows.length > 0 || fixed.length > 0) && (
            <Group>
              <GroupHead
                title="Builder chat can fix"
                sub="The builder chat can make these changes"
                count={builderRows.length + fixed.length}
                icon="solar:chat-round-dots-linear"
                lead={
                  builderRows.length > 0 ? (
                    <Checkbox
                      size="small"
                      disabled={!!chatJobs}
                      checked={ticked.length === builderRows.length}
                      indeterminate={
                        ticked.length > 0 && ticked.length < builderRows.length
                      }
                      onChange={(e) =>
                        setUnticked(
                          e.target.checked ? [] : builderRows.map((a) => a.id),
                        )
                      }
                      inputProps={{ "aria-label": "Tick every builder fix" }}
                      sx={{ p: 0.25, ml: -0.5 }}
                    />
                  ) : null
                }
                action={
                  builderRows.length > 0 && (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={!picked.length || !!chatJobs || checking}
                      onClick={() => startBuilder(picked)}
                      sx={{ py: 0, flexShrink: 0 }}
                    >
                      {chatJobs
                        ? "Fixing…"
                        : picked.length
                          ? `Fix ${picked.length} with builder chat`
                          : "Tick lines to fix"}
                    </Button>
                  )
                }
              />
              {builderRows.map((a) => (
                <Row
                  key={a.id}
                  area={a}
                  open={openId === a.id}
                  onToggle={() => toggle(a.id)}
                  lead={
                    <Checkbox
                      size="small"
                      disabled={!!chatJobs}
                      checked={!unticked.includes(a.id)}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) =>
                        setUnticked((u) =>
                          e.target.checked
                            ? u.filter((x) => x !== a.id)
                            : [...u, a.id],
                        )
                      }
                      inputProps={{ "aria-label": `Fix ${a.label}` }}
                      sx={{ p: 0.25, ml: -0.5 }}
                    />
                  }
                  end={
                    fixing.has(a.id) ? (
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "primary.main",
                          flexShrink: 0,
                        }}
                      >
                        Fixing…
                      </Typography>
                    ) : (
                      blockedNote(a)
                    )
                  }
                >
                  {factsOf(a)}
                  <Box>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
                    >
                      Sends this to the builder chat
                    </Typography>
                    <Box
                      sx={{
                        px: 1.25,
                        py: 0.75,
                        borderRadius: "6px",
                        bgcolor: "background.neutral",
                        typography: "s2",
                      }}
                    >
                      {builderPromptFor(kindOf(a), a.fixRow)}
                    </Box>
                  </Box>
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={!!chatJobs || checking}
                    onClick={() => startBuilder([a])}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    Fix just this one
                  </Button>
                </Row>
              ))}
              {fixed.map((f) => (
                <Stack
                  key={`fixed-${f.id}`}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.5,
                    py: 0.625,
                    pl: 4.75,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <StatusDot status="passed" size={6} />
                  <Typography sx={{ typography: "s2", flex: 1 }}>
                    {f.label}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "success.main" }}>
                    Fixed
                  </Typography>
                </Stack>
              ))}
              {chatJobs && (
                <BuilderFixChat
                  jobs={chatJobs}
                  onDone={builderDone}
                  onClose={() => setChatJobs(null)}
                />
              )}
            </Group>
          )}

          {/* ── Re-check to confirm: not checked yet, or checked on another version ── */}
          {recheckRows.length > 0 && (
            <Group>
              <GroupHead
                icon="solar:refresh-linear"
                title="Re-check to confirm"
                sub="Checking again settles these"
                count={recheckRows.length}
                action={
                  onCheck && (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={checking}
                      onClick={onCheck}
                      sx={{ py: 0, flexShrink: 0 }}
                    >
                      {checking
                        ? "Checking…"
                        : val
                          ? "Re-check"
                          : "Run the check"}
                    </Button>
                  )
                }
              />
              {recheckRows.map((a) => (
                <Row
                  key={a.id}
                  area={a}
                  open={openId === a.id}
                  onToggle={() => toggle(a.id)}
                >
                  <Facts
                    rows={[
                      ["What it checks", CHECKS[a.id]],
                      [
                        "Last check",
                        state === "stale"
                          ? report.staleWhy
                          : a.waits
                            ? `Waits on ${String(a.fixRow?.blockedBy || "Starts and replies").toLowerCase()}`
                            : val?.at
                              ? `The last check (${ago(val.at)}) didn't cover it`
                              : "Never checked for this agent",
                      ],
                      [
                        "Why again",
                        a.waits
                          ? "It settles once that check passes; nothing to do here."
                          : state === "stale"
                            ? "The result may differ on this version. Check again before trusting the scenarios that need it."
                            : "Until it's checked, results that need it can't be trusted.",
                      ],
                      [
                        "Waits on it",
                        a.affected?.length
                          ? namesOf(a.affected, titleOf)
                          : null,
                      ],
                    ]}
                  />
                </Row>
              ))}
            </Group>
          )}
        </Stack>
      )}

      {ready.length > 0 && (
        <Tooltip
          arrow
          placement="top-start"
          title={ready.map((a) => a.label).join(" · ")}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 0.625,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <StatusDot
              status={checking ? "running" : "passed"}
              size={6}
              live={checking}
            />
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {blockers.length || recheckRows.length
                ? `${ready.length} more ready`
                : `All ${ready.length} ready`}
            </Typography>
          </Stack>
        </Tooltip>
      )}

      {(limits.length > 0 || voiceReplies) && !checking && (
        <Tooltip
          arrow
          placement="top-start"
          title={
            <Box sx={{ whiteSpace: "pre-line" }}>
              {[
                ...limits.map((a) => `${a.label}: ${limitPhrase(a)}`),
                ...(voiceReplies
                  ? [`${voiceReplies.label}: ${voiceReplies.detail}`]
                  : []),
                "These come from where the agent runs, not from your setup — there's nothing to fix. Runs go ahead, and each run's results carry the same note.",
              ].join("\n\n")}
            </Box>
          }
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 0.625,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:info-circle-linear"
              width={14}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {(() => {
                const n = limits.length + (voiceReplies ? 1 : 0);
                return `${limitBy === "Hosting" ? "Runs outside our sandbox" : `Runs on ${limitBy}`} · ${n} known limit${n === 1 ? "" : "s"}`;
              })()}
            </Typography>
          </Stack>
        </Tooltip>
      )}

      {children && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {children}
        </Box>
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
  children: PropTypes.node,
};
