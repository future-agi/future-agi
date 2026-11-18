import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  ButtonBase,
  MenuItem,
  Select,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { TextLink } from "../agents/connect/connectBits";
import Iconify from "src/components/iconify";
import { agentToolNamesOf } from "../_mock/worldFit";

/* Fit as an icon — the words are on hover, so the table stays quiet. */
const fitIconOf = (r) =>
  r.needsInput || (r.kind === "mapped" && !r.reviewed)
    ? {
        icon: "solar:pen-new-square-linear",
        tone: "status.warning.fg",
        tip: "Needs input — the names line up, but something differs. Review it below.",
      }
    : r.kind === "missing"
      ? {
          icon: "solar:minus-circle-linear",
          tone: "status.warning.fg",
          tip: "Not available — your agent has no matching tool, so its cases can't be measured.",
        }
      : {
          icon: "solar:check-circle-linear",
          tone: "status.success.fg",
          tip: r.kind === "mapped" ? "Mapped and reviewed" : "Matched by name",
        };

/**
 * Review fit (roadmap catalogue.md): your agent against the world's fixed
 * suite. Each world tool binds to one of your agent's tools — matched by
 * name, mapped by meaning (to review), or not available, whose cases stay
 * listed as unmeasured. The suite is never rewritten to make the agent fit.
 */
const VERDICT = {
  ready: {
    label: "Ready",
    icon: "solar:check-circle-linear",
    tone: "status.success.fg",
  },
  needs_input: {
    label: "Needs input",
    icon: "solar:pen-new-square-linear",
    tone: "status.warning.fg",
  },
  partial: {
    label: "Partial",
    icon: "solar:danger-triangle-linear",
    tone: "status.warning.fg",
  },
};

export default function WorldFitReport({
  template,
  agent,
  fit,
  onBind,
  onReview,
  onOtherWorld,
}) {
  const agentTools = agentToolNamesOf(agent);
  /* Plain name matches need no look — folded unless opened. */
  const [showMatched, setShowMatched] = useState(false);
  const [showUnmeasured, setShowUnmeasured] = useState(false);
  const isPlainMatch = (r) => r.kind === "matched" && !r.needsInput;
  const matched = fit.rows.filter(isPlainMatch);
  const shown = showMatched
    ? fit.rows
    : fit.rows.filter((r) => !isPlainMatch(r));
  const total = fit.total;
  return (
    <Stack spacing={1.5}>
      {/* The verdict first: Ready runs the whole supported suite; Partial says what isn't measured. */}
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon={VERDICT[fit.verdict].icon}
          width={16}
          sx={{ color: VERDICT[fit.verdict].tone, flexShrink: 0, mt: "2px" }}
        />
        <Typography sx={{ typography: "s1" }}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {/* Nothing measurable isn't partial: say so plainly. */}
            {fit.verdict === "partial" &&
            fit.total > 0 &&
            fit.unmeasured.length === fit.total
              ? "Can't be measured"
              : VERDICT[fit.verdict].label}
          </Box>
          {fit.verdict === "ready"
            ? ` — all ${total} cases run on ${agent.name}, unchanged.`
            : fit.verdict === "needs_input"
              ? ` — ${fit.needsInput.length} mapping${fit.needsInput.length === 1 ? "" : "s"} to review: the names line up, but ${fit.needsInput.length === 1 ? "something differs" : "things differ"} underneath. Review ${fit.needsInput.length === 1 ? "it" : "them"} below before the run.`
              : fit.total > 0 && fit.unmeasured.length === fit.total
                ? ` — ${agent.name} has none of this world's tools. Pair one below, or choose another world.`
                : ` — ${fit.unmeasured.length} of ${total} cases can't be measured${fit.channel.ok ? "" : `, and ${agent.name} is a ${fit.channel.agent} agent in a ${fit.channel.world} world`}. They stay listed, never dropped.`}
        </Typography>
      </Stack>
      {/* Why this table exists, in one line. */}
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: -0.5 }}>
        A case can only be graded if your agent has the tool it needs — pair
        each world tool with yours.
      </Typography>

      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        <Stack
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              width: 190,
              flexShrink: 0,
            }}
          >
            World tool
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >{`${agent.name}'s tool`}</Typography>
          <Box sx={{ width: 20, flexShrink: 0 }} />
        </Stack>
        {/* Matched rows fold into one line; what needs a look stays open. */}
        {matched.length > 0 && (
          <ButtonBase
            onClick={() => setShowMatched((v) => !v)}
            sx={{
              width: 1,
              justifyContent: "flex-start",
              gap: 1,
              px: 1.5,
              py: 0.75,
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Iconify
              icon="solar:check-circle-linear"
              width={16}
              sx={{ color: "status.success.fg" }}
            />
            <Typography sx={{ typography: "s1", flex: 1, textAlign: "left" }}>
              {`${matched.length} matched`}
            </Typography>
            <Iconify
              icon={
                showMatched
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={14}
              sx={{ color: "text.tertiary" }}
            />
          </ButtonBase>
        )}
        {shown.map((r, i) => (
          <Box
            key={r.world}
            sx={{
              ...((i > 0 || matched.length > 0) && {
                borderTop: "1px solid",
                borderColor: "divider",
              }),
            }}
          >
            <Stack
              direction="row"
              spacing={1.5}
              alignItems="center"
              sx={{
                px: 1.5,
                py: 0.5,
              }}
            >
              <Typography
                noWrap
                sx={{
                  typography: "s1",
                  fontFamily: "monospace",
                  width: 190,
                  flexShrink: 0,
                }}
              >
                {r.world}
              </Typography>
              <Box flex={1} minWidth={0}>
                <Select
                  size="small"
                  fullWidth
                  value={r.agent || ""}
                  displayEmpty
                  onChange={(e) => onBind(r.world, e.target.value || null)}
                  sx={{
                    typography: "s1",
                    fontFamily: "monospace",
                    "& .MuiSelect-select": { py: 0.5 },
                  }}
                >
                  <MenuItem value="" sx={{ typography: "s1" }}>
                    Not available
                  </MenuItem>
                  {agentTools.map((a) => (
                    <MenuItem
                      key={a}
                      value={a}
                      sx={{ typography: "s1", fontFamily: "monospace" }}
                    >
                      {a}
                    </MenuItem>
                  ))}
                </Select>
              </Box>
              <Tooltip arrow title={fitIconOf(r).tip}>
                <Box
                  sx={{
                    width: 20,
                    flexShrink: 0,
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  <Iconify
                    icon={fitIconOf(r).icon}
                    width={16}
                    sx={{ color: fitIconOf(r).tone }}
                    aria-label={fitIconOf(r).tip}
                  />
                </Box>
              </Tooltip>
            </Stack>
            {/* A mapping: identity, units and refusals as well as fields — differences stay visible. */}
            {r.kind === "mapped" && r.checks && (
              <Box sx={{ px: 1.5, pb: 0.75, pl: "calc(190px + 24px)" }}>
                {r.checks.map((c) => (
                  <Typography
                    key={c.id}
                    sx={{
                      typography: "s3",
                      color: c.ok ? "text.tertiary" : "text.secondary",
                    }}
                  >
                    <Box
                      component="span"
                      sx={{
                        fontWeight: 600,
                        color: c.ok ? "text.tertiary" : "status.warning.fg",
                      }}
                    >
                      {`${c.label}: `}
                    </Box>
                    {c.line}
                    {!c.ok && c.fix ? ` — ${c.fix}.` : ""}
                  </Typography>
                ))}
                {r.differs?.length > 0 && onReview && (
                  <TextLink onClick={() => onReview(r.world, !r.reviewed)}>
                    {r.reviewed
                      ? "Undo review"
                      : "Reviewed — run with this mapping"}
                  </TextLink>
                )}
              </Box>
            )}
          </Box>
        ))}
      </Box>

      {fit.mapped.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Check each mapping for identity and refusals, not just fields: ${fit.mapped[0].agent} must behave like ${fit.mapped[0].world}.`}
        </Typography>
      )}

      {fit.extra.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`${agent.name}'s ${fit.extra.join(", ")} ${fit.extra.length === 1 ? "isn't" : "aren't"} in this world — ${fit.live ? "calls to them reach your real systems on a live run" : "the world doesn't answer them, so a case that calls them fails"}.`}
        </Typography>
      )}

      {fit.liveActions?.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Runs for real on ${agent.name}'s platform: ${fit.liveActions.map((t) => `${t.name} (${t.line.charAt(0).toLowerCase()}${t.line.slice(1)})`).join(" · ")}. The world can't redirect platform actions.`}
        </Typography>
      )}

      {/* Only the unusual case is said: a live run checks the starting state only. */}
      {fit.live && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Live: only the starting state is checked.
        </Typography>
      )}

      {/* Partial: what isn't measured, and how to repair it. */}
      {fit.unmeasured.length > 0 && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 1.5,
            py: 1,
          }}
        >
          <Stack direction="row" alignItems="baseline" spacing={1.5}>
            <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1 }}>
              {`${fit.unmeasured.length} ${fit.unmeasured.length === 1 ? "case" : "cases"} can't be measured with ${agent.name}`}
            </Typography>
            {/* Which cases: on request, not a list by default. */}
            <TextLink onClick={() => setShowUnmeasured((v) => !v)}>
              {showUnmeasured ? "Hide" : "Show which"}
            </TextLink>
            {onOtherWorld && (
              <TextLink onClick={onOtherWorld}>Choose another world</TextLink>
            )}
          </Stack>
          {showUnmeasured &&
            fit.unmeasured.map((s) => (
              <Typography
                key={s.id}
                noWrap
                sx={{ typography: "s3", color: "text.secondary" }}
              >
                {s.title}
              </Typography>
            ))}
        </Box>
      )}
    </Stack>
  );
}
WorldFitReport.propTypes = {
  template: PropTypes.object.isRequired,
  agent: PropTypes.object.isRequired,
  fit: PropTypes.object.isRequired,
  onBind: PropTypes.func.isRequired,
  /* (worldTool, reviewed) — a mapping whose differences someone accepted. */
  onReview: PropTypes.func,
  onOtherWorld: PropTypes.func,
};
