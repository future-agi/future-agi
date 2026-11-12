import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  InputBase,
  Menu,
  MenuItem,
  Popover,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import KeyCap from "src/sections/common/ux/KeyCap";

const CHOICES = [
  { choice: "left", key: "1", action: "verdictBaseline" },
  { choice: "tie", key: "2", action: "verdictTie", label: "Tie" },
  { choice: "right", key: "3", action: "verdictOther" },
  { choice: "both_bad", key: "4", action: "verdictBothBad", label: "Both bad" },
];

/**
 * `Rate ▾` (menu: 1 #2B did better · 2 Tie · 3 #2D did better · 4 Both
 * bad; keys 1–4 rate directly) — compares the left lane (baseline, or Run A
 * in Blind) with the focused lane. After a
 * choice an optional `Why?` (Enter saves); in Blind, `Reveal` maps names.
 */
export default function VerdictBar({
  left,
  right,
  current,
  onChoose,
  onReason,
  blind,
  reveal,
  onReveal,
  revealed,
  names,
  terse,
}) {
  const [why, setWhy] = useState("");
  const compact = useMediaQuery("(max-width:1366px)");
  const [whyOpen, setWhyOpen] = useState(false);
  const whyBtn = useRef(null);
  const barRef = useRef(null);
  useEffect(() => setWhy(""), [current?.at]);
  // Toasts (Saved · Undo ⌘Z) sit just above this bar, never on the keys
  // they confirm: reserve the distance from the bar's top to the bottom.
  useEffect(() => {
    const body = document.body;
    const sync = () => {
      const r = barRef.current?.parentElement?.getBoundingClientRect();
      // Only a bar anchored to the bottom of the window (the workspace dock).
      if (!r || window.innerHeight - r.bottom > 24) {
        body.style.removeProperty("--fa-toast-offset");
        return;
      }
      const gap = Math.max(0, Math.round(window.innerHeight - r.top) + 4);
      body.style.setProperty("--fa-toast-offset", `${gap}px`);
    };
    sync();
    window.addEventListener("resize", sync);
    return () => {
      window.removeEventListener("resize", sync);
      body.style.removeProperty("--fa-toast-offset");
    };
  }, []);
  const nameOf = (l) => l?.blindName ?? names?.get(l?.id) ?? l?.label ?? "";
  const [rateAnchor, setRateAnchor] = useState(null);
  const labelOf = (c) =>
    c.label ?? `${c.choice === "left" ? nameOf(left) : nameOf(right)} better`;
  const titleOf = (c) =>
    c.choice === "left"
      ? `${nameOf(left)} did better`
      : c.choice === "right"
        ? `${nameOf(right)} did better`
        : c.choice === "tie"
          ? "Tie · both did equally well"
          : "Both bad · neither did the task well";
  return (
    <Stack
      ref={barRef}
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ minWidth: 0, flexShrink: 1 }}
    >
      {/* IT-R1 G4: one `Rate ▾` instead of four buttons; keys 1–4 still
          rate from anywhere (the workspace shortcuts). */}
      <KeyHintTooltip
        title="Which did better? 1 left · 2 tie · 3 right · 4 both bad"
        action="verdictBaseline"
      >
        <Button
          size="small"
          variant={current ? "contained" : "outlined"}
          color={current ? "primary" : "inherit"}
          onClick={(e) => setRateAnchor(e.currentTarget)}
          aria-haspopup="menu"
          aria-expanded={Boolean(rateAnchor)}
          data-testid="tc-rate"
          endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
          sx={{
            minWidth: 0,
            px: 1,
            py: 0.125,
            typography: "s2",
            borderColor: "divider",
            whiteSpace: "nowrap",
            flexShrink: 1,
            maxWidth: 320,
            overflow: "hidden",
          }}
        >
          <Box
            component="span"
            sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {current
              ? `Rated: ${labelOf(CHOICES.find((c) => c.choice === current.choice) ?? CHOICES[1])}`
              : (blind && compact) || terse
                ? "Rate"
                : "Rate · which did better?"}
          </Box>
        </Button>
      </KeyHintTooltip>
      <Menu
        anchorEl={rateAnchor}
        open={Boolean(rateAnchor)}
        onClose={() => setRateAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        MenuListProps={{ dense: true, "aria-label": "Which did better?" }}
      >
        {CHOICES.map((c) => (
          <MenuItem
            key={c.choice}
            selected={current?.choice === c.choice}
            onClick={() => {
              setRateAnchor(null);
              onChoose(c.choice);
            }}
            sx={{ gap: 1, typography: "s2" }}
          >
            <KeyCap sx={{ flexShrink: 0 }}>{c.key}</KeyCap>
            <Box component="span" sx={{ maxWidth: 320 }}>
              {titleOf(c)}
            </Box>
          </MenuItem>
        ))}
      </Menu>
      {current ? (
        <>
          <CustomTooltip
            describeChild
            show
            size="small"
            title={
              current.reason
                ? `Why: ${current.reason}`
                : "Add an optional reason (Enter saves)"
            }
          >
            <Button
              ref={whyBtn}
              size="small"
              color="inherit"
              onClick={() => setWhyOpen(true)}
              startIcon={
                <Iconify
                  icon={
                    current.reason
                      ? "mdi:comment-check-outline"
                      : "mdi:comment-question-outline"
                  }
                  width={14}
                />
              }
              sx={{
                flexShrink: 0,
                color: "text.secondary",
                minWidth: 0,
                whiteSpace: "nowrap",
              }}
            >
              {compact || blind
                ? null
                : current.reason
                  ? "Reason saved"
                  : "Why?"}
            </Button>
          </CustomTooltip>
          <Popover
            open={whyOpen}
            anchorEl={whyBtn.current}
            onClose={() => setWhyOpen(false)}
            anchorOrigin={{ vertical: "top", horizontal: "left" }}
            transformOrigin={{ vertical: "bottom", horizontal: "left" }}
          >
            <Box sx={{ p: 1, width: 320 }}>
              <InputBase
                autoFocus
                value={why}
                onChange={(e) => setWhy(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") e.stopPropagation();
                  if (e.key === "Enter" && why.trim()) {
                    onReason(why.trim());
                    setWhy("");
                    setWhyOpen(false);
                  }
                }}
                placeholder={
                  current.reason
                    ? `Why: ${current.reason}`
                    : "Why did it do better? (Enter saves)"
                }
                inputProps={{ "aria-label": "Why?" }}
                sx={{
                  typography: "s2",
                  px: 0.75,
                  height: 30,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                  width: "100%",
                }}
              />
            </Box>
          </Popover>
        </>
      ) : null}
      {blind && current ? (
        revealed ? (
          <CustomTooltip describeChild show size="small" title={reveal}>
            <Typography
              typography="s2"
              fontWeight={600}
              noWrap
              sx={{ minWidth: 60, flexShrink: 1, color: "text.primary" }}
            >
              {reveal.replace(/Run ([A-Z]) was /g, "$1 = ")}
            </Typography>
          </CustomTooltip>
        ) : (
          <Button
            size="small"
            onClick={onReveal}
            startIcon={<Iconify icon="mdi:eye-outline" width={14} />}
            sx={{ flexShrink: 0 }}
          >
            Reveal
          </Button>
        )
      ) : null}
    </Stack>
  );
}

VerdictBar.propTypes = {
  left: PropTypes.object,
  right: PropTypes.object,
  current: PropTypes.object,
  onChoose: PropTypes.func.isRequired,
  onReason: PropTypes.func.isRequired,
  blind: PropTypes.bool,
  reveal: PropTypes.string,
  onReveal: PropTypes.func,
  revealed: PropTypes.bool,
  names: PropTypes.instanceOf(Map),
  /** Below 1600 px: `Better?` keeps the run names whole. */
  terse: PropTypes.bool,
};
