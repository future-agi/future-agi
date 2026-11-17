import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import Truncate from "src/sections/common/ux/Truncate";
import { fmtCost, fmtMs } from "src/sections/common/format/fmt";
import { VerdictChip } from "./LaneHeader";
import { MAX_LANES, kindChip, trialMetrics } from "./workspaceModel";
import OneLine from "./OneLine";
import { firstWrongLineOf } from "./whyModel";

const MINI = {
  light: {
    customer: "#9CA3AF",
    reply: "#3B82F6",
    tool: "#0D9488",
    retrieval: "#8B5CF6",
    end: "#475569",
    think: "#CBD5E1",
  },
  dark: {
    customer: "#6B7280",
    reply: "#60A5FA",
    tool: "#2DD4BF",
    retrieval: "#A78BFA",
    end: "#94A3B8",
    think: "#475569",
  },
};

function MiniRibbon({ steps }) {
  const theme = useTheme();
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  return (
    <CustomTooltip
      describeChild
      show
      size="small"
      title={`${steps.length} steps · ${steps.filter((s) => s.error).length} errors`}
    >
      <Stack direction="row" sx={{ gap: "2px", height: 8, mt: 1 }}>
        {steps.map((s) => (
          <Box
            key={s.n}
            sx={{
              flex: 1,
              minWidth: 2,
              borderRadius: "1px",
              bgcolor: s.error
                ? "error.main"
                : MINI[mode][s.kind] ?? MINI[mode].think,
            }}
          />
        ))}
      </Stack>
    </CustomTooltip>
  );
}
MiniRibbon.propTypes = { steps: PropTypes.array.isRequired };

/**
 * Cards view (5+ runs): one card per run (compact header, final answer 6
 * lines, mini ribbon) with `Promote to lane`; a one-click CTA applies the
 * default lane rule.
 */
const FAILED = new Set(["fail", "flaky", "error"]);

export default function CardsView({
  lanes,
  defaultIds,
  onPromote,
  onOpenSteps,
}) {
  const [picked, setPicked] = useState([]);
  const toggle = (id) =>
    setPicked((p) =>
      p.includes(id)
        ? p.filter((x) => x !== id)
        : p.length < MAX_LANES
          ? [...p, id]
          : p,
    );
  const ctaLabel = `Compare the ${defaultIds.length} most different side by side · shows the first wrong step`;
  return (
    <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ mb: 1.5, flexWrap: "wrap", rowGap: 1 }}
      >
        <Iconify
          icon="mdi:view-grid-outline"
          width={18}
          sx={{ color: "text.secondary" }}
        />
        <Typography typography="s1">
          {lanes.length} runs. Click any{" "}
          {MAX_LANES > 2 ? `2 to ${MAX_LANES}` : "2"} cards to compare them side
          by side.
        </Typography>
        <Box sx={{ flex: 1 }} />
        {/* IT-R4 G4 P5: a short button, its detail in the tooltip (the
            long label read as a black banner, not an action). */}
        <CustomTooltip describeChild show size="small" title={ctaLabel}>
          <span>
            <Button
              variant={picked.length >= 2 ? "outlined" : "contained"}
              color="primary"
              size="small"
              onClick={() => onPromote(defaultIds)}
              disabled={defaultIds.length < 2}
              aria-label={ctaLabel}
              startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
            >
              {`Compare the ${defaultIds.length} most different`}
            </Button>
          </span>
        </CustomTooltip>
        {/* IT-R5 G4 P4: helper text until 2 are picked (never a disabled
            `Compare 0 selected`). */}
        {picked.length >= 2 ? (
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={() => onPromote(picked)}
            data-testid="cards-compare-selected"
          >
            Compare {picked.length} selected
          </Button>
        ) : (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="cards-compare-help"
          >
            {MAX_LANES > 2 ? `Select 2 to ${MAX_LANES}` : "Select 2"}
          </Typography>
        )}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 1.5,
        }}
      >
        {lanes.map((l) => {
          const m = trialMetrics(l.trial);
          const on = picked.includes(l.id);
          const disabled = !on && picked.length >= MAX_LANES;
          // fr-5: a failed card opens its steps at the first
          // wrong step and says which step it was.
          const failed = FAILED.has(l.verdict?.key) && onOpenSteps;
          const fwLine = failed ? firstWrongLineOf(l) : null;
          return (
            <Box
              key={l.id}
              // IT-R2 G4 P9: a click picks the card (its checkbox), never a
              // silent jump; `Open steps at step N` is its own button.
              role="checkbox"
              aria-checked={on}
              aria-disabled={disabled || undefined}
              tabIndex={0}
              aria-label={`Compare ${l.label}`}
              data-failed-card={failed ? "" : undefined}
              data-run-card={l.id}
              onClick={(e) => {
                if (e.target.closest("label, button, input, a")) return;
                if (!disabled) toggle(l.id);
              }}
              onKeyDown={(e) => {
                if (
                  (e.key === "Enter" || e.key === " ") &&
                  e.target === e.currentTarget
                ) {
                  e.preventDefault();
                  if (!disabled) toggle(l.id);
                }
              }}
              sx={{
                cursor: disabled ? "default" : "pointer",
                "&:hover": disabled ? undefined : { boxShadow: 2 },
                outline: on ? "2px solid" : "none",
                outlineColor: l.color?.solid,
                outlineOffset: -1,
                border: "1px solid",
                borderColor: on ? l.color?.solid : "divider",
                borderTop: "3px solid",
                borderTopColor: l.color?.solid,
                borderRadius: 1,
                p: 1.25,
                bgcolor: "background.paper",
                display: "flex",
                flexDirection: "column",
                minWidth: 0,
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                sx={{ minWidth: 0 }}
              >
                <Typography
                  typography="s1"
                  fontWeight={700}
                  sx={{ flexShrink: 0 }}
                >
                  {l.label}
                </Typography>
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <OneLine
                    text={l.name ?? ""}
                    sx={{ typography: "s2", color: "text.secondary" }}
                  />
                </Box>
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ flexShrink: 0 }}
                >
                  {kindChip(l.kind)}
                </Typography>
              </Stack>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mt: 0.75 }}
              >
                <VerdictChip verdict={l.verdict} />
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {m.steps} steps · {fmtCost(m.cost)} · {fmtMs(m.wall ?? 0)}
                </Typography>
              </Stack>
              <MiniRibbon steps={l.steps ?? []} />
              {fwLine ? (
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ mt: 0.75, minWidth: 0 }}
                >
                  <Typography
                    typography="s2"
                    color="error.main"
                    fontWeight={600}
                    sx={{ minWidth: 0, flex: 1 }}
                    data-testid="card-first-wrong"
                  >
                    {fwLine}
                  </Typography>
                  <Button
                    size="small"
                    onClick={() => onOpenSteps(l.id)}
                    sx={{
                      flexShrink: 0,
                      typography: "s2",
                      whiteSpace: "nowrap",
                    }}
                  >
                    Open its steps
                  </Button>
                </Stack>
              ) : null}
              <Box sx={{ mt: 1, flex: 1 }}>
                {l.trial?.final_answer ? (
                  <Truncate
                    text={l.trial.final_answer}
                    lines={6}
                    sx={{ typography: "s2" }}
                  />
                ) : (
                  <Typography typography="s2" color="text.disabled">
                    No reply to the customer
                  </Typography>
                )}
              </Box>
              <CustomTooltip
                describeChild
                show
                size="small"
                title={
                  disabled
                    ? `Up to ${MAX_LANES} side by side · unpick one first`
                    : "Pick this run to compare it"
                }
              >
                <FormControlLabel
                  sx={{ mt: 0.5, ml: 0 }}
                  control={
                    <Checkbox
                      size="small"
                      checked={on}
                      disabled={disabled}
                      onChange={() => toggle(l.id)}
                    />
                  }
                  label={<Typography typography="s2">Compare</Typography>}
                />
              </CustomTooltip>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

CardsView.propTypes = {
  lanes: PropTypes.array.isRequired,
  defaultIds: PropTypes.array.isRequired,
  onPromote: PropTypes.func.isRequired,
  onOpenSteps: PropTypes.func,
};
