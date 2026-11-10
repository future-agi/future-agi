import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, InputBase, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import ErrorStatusChip from "./ErrorStatusChip";
import {
  accentSx,
  escalateAt,
  explainLimit,
  focusRing,
  recoverAt,
} from "./escalationSettings";

const MAX_USUAL = 1_000_000;
const fmt = (n) => n.toLocaleString("en-US");

function Token({ children, tip }) {
  const token = (
    <Box
      component="b"
      tabIndex={tip ? 0 : undefined}
      sx={{
        display: "inline-block",
        px: 0.75,
        borderRadius: 0.5,
        ...accentSx,
        fontWeight: 600,
        lineHeight: "22px",
        fontVariantNumeric: "tabular-nums",
        cursor: tip ? "help" : "default",
        ...(tip && {
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
          },
        }),
      }}
    >
      {children}
    </Box>
  );
  return tip ? (
    <CustomTooltip show title={tip} size="small" arrow placement="top">
      {token}
    </CustomTooltip>
  ) : (
    token
  );
}
Token.propTypes = { children: PropTypes.node, tip: PropTypes.string };

function UsualStepper({ value, onChange }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = (n) => {
    const next = Math.min(Math.max(Math.round(n), 1), MAX_USUAL);
    setText(String(next));
    onChange(next);
  };
  const stepButton = (dir, label) => (
    <ButtonBase
      aria-label={label}
      onClick={() => commit(value + dir)}
      sx={{
        width: 22,
        height: "100%",
        color: "primary.main",
        fontSize: 15,
        "&:hover": { bgcolor: accentSx.bgcolor },
      }}
    >
      {dir < 0 ? "−" : "+"}
    </ButtonBase>
  );
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        height: 24,
        mx: 0.25,
        verticalAlign: "middle",
        border: "1px solid",
        borderColor: "primary.main",
        borderRadius: 0.75,
        bgcolor: "background.paper",
        overflow: "hidden",
        "&:focus-within": { boxShadow: focusRing },
      }}
    >
      {stepButton(-1, "Fewer findings an hour")}
      <InputBase
        value={text}
        onChange={(event) => {
          const digits = event.target.value.replace(/\D/g, "").slice(0, 7);
          setText(digits);
          if (digits) commit(Number(digits));
        }}
        onBlur={() => setText(String(value))}
        onKeyDown={(event) => {
          // Preview only: Enter must not submit the settings form.
          if (event.key === "Enter") {
            event.preventDefault();
            event.currentTarget.blur();
          } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            const step = event.shiftKey ? 10 : 1;
            commit(value + (event.key === "ArrowUp" ? step : -step));
          }
        }}
        inputProps={{
          inputMode: "numeric",
          "aria-label": "Usual findings an hour (preview only)",
          style: {
            width: `${Math.max(2, text.length + 0.3)}ch`,
            padding: 0,
            textAlign: "center",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          },
        }}
        sx={{ color: "primary.main", fontSize: "inherit" }}
      />
      {stepButton(1, "More findings an hour")}
    </Box>
  );
}
UsualStepper.propTypes = {
  value: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
};

function Ruler({ escalate, recover, usual }) {
  const max = Math.max(escalate * 1.4, usual * 1.2);
  // Zone edges sit just past the last recovering and before the first escalating count.
  const pRec = Math.min(100, ((recover + 0.5) / max) * 100);
  const pEnt = Math.min(100, ((escalate - 0.5) / max) * 100);
  const pUsual = Math.min(100, (usual / max) * 100);
  const marker = (pct) => ({
    position: "absolute",
    left: `${pct}%`,
    transform:
      pct < 9 ? "none" : pct > 91 ? "translateX(-100%)" : "translateX(-50%)",
    whiteSpace: "nowrap",
    fontSize: 11,
    lineHeight: "16px",
    fontVariantNumeric: "tabular-nums",
    transition: "left 0.45s cubic-bezier(.2,.8,.2,1)",
  });
  const zone = {
    height: "100%",
    display: "flex",
    alignItems: "center",
    px: 1,
    fontSize: 11,
    fontWeight: 500,
    whiteSpace: "nowrap",
    overflow: "hidden",
    flex: "none",
    transition: "width 0.45s cubic-bezier(.2,.8,.2,1)",
  };
  return (
    <Box aria-hidden sx={{ position: "relative", height: 74, mt: 1.25 }}>
      <Box
        sx={{
          position: "absolute",
          inset: "24px 0 auto",
          height: 24,
          borderRadius: 0.75,
          overflow: "hidden",
          display: "flex",
          bgcolor: "action.hover",
        }}
      >
        <Box
          sx={{
            ...zone,
            width: `${pRec}%`,
            bgcolor: (t) =>
              t.palette.mode === "dark" ? "rgba(245,166,35,0.15)" : "#FDEEE2",
            color: (t) => (t.palette.mode === "dark" ? "#F5A623" : "#8C3F08"),
          }}
        >
          For review
        </Box>
        <Box
          sx={{
            ...zone,
            width: `${Math.max(0, pEnt - pRec)}%`,
            justifyContent: "center",
            color: "text.secondary",
            background: (t) =>
              `repeating-linear-gradient(135deg, transparent 0 5px, ${t.palette.divider} 5px 6px)`,
          }}
        >
          {pEnt - pRec > 18 && "No change"}
        </Box>
        <Box
          sx={{
            ...zone,
            flex: 1,
            justifyContent: "flex-end",
            bgcolor: (t) =>
              t.palette.mode === "dark" ? "rgba(219,47,45,0.15)" : "#FCE8E7",
            color: (t) => (t.palette.mode === "dark" ? "#E87878" : "#DB2F2D"),
          }}
        >
          Escalating
        </Box>
      </Box>
      <Box sx={{ ...marker(pRec), top: 4, fontWeight: 600 }}>
        ≤ {fmt(recover)}
      </Box>
      <Box sx={{ ...marker(pEnt), top: 4, fontWeight: 600 }}>
        {fmt(escalate)}+
      </Box>
      <Box
        sx={{
          position: "absolute",
          top: 20,
          left: `${pUsual}%`,
          width: 2,
          height: 32,
          ml: "-1px",
          bgcolor: "text.primary",
          borderRadius: 0.5,
          transition: "left 0.45s cubic-bezier(.2,.8,.2,1)",
        }}
      />
      <Box sx={{ ...marker(pUsual), top: 54, color: "text.secondary" }}>
        usual {fmt(usual)}
      </Box>
    </Box>
  );
}
Ruler.propTypes = {
  escalate: PropTypes.number.isRequired,
  recover: PropTypes.number.isRequired,
  usual: PropTypes.number.isRequired,
};

export default function EscalationSummary({
  values,
  usual,
  onUsualChange,
  stale,
  baselineHours,
}) {
  const escalate = escalateAt(values, usual);
  const recover = recoverAt(values, usual);
  const nowrap = { whiteSpace: "nowrap" };
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.neutral",
        p: 2,
        pb: 1.75,
      }}
    >
      <Box sx={{ opacity: stale ? 0.45 : 1, transition: "opacity 0.2s" }}>
        <Typography
          component="div"
          aria-live="polite"
          sx={{
            fontSize: 14,
            lineHeight: "30px",
            "& .MuiChip-root": { verticalAlign: "middle" },
          }}
        >
          For example, an issue that{" "}
          <CustomTooltip
            show
            size="small"
            arrow
            placement="top"
            title={`"Usual" is the average findings an hour over the previous ${baselineHours} hours. It stays fixed while an issue is escalating, so the spike does not raise it.`}
          >
            <Box
              component="span"
              tabIndex={0}
              sx={{
                borderBottom: "1px dotted",
                borderColor: "text.secondary",
                cursor: "help",
              }}
            >
              usually gets
            </Box>
          </CustomTooltip>{" "}
          <UsualStepper value={usual} onChange={onUsualChange} /> findings an
          hour becomes <ErrorStatusChip status="escalating" />{" "}
          <Box component="span" sx={nowrap}>
            after <Token>{values.entry_confirmation_minutes} minutes</Token>
          </Box>{" "}
          <Box component="span" sx={nowrap}>
            at{" "}
            <Token tip={explainLimit(values, usual, "entry")}>
              {fmt(escalate)}+
            </Token>
          </Box>{" "}
          an hour, and goes back to <ErrorStatusChip status="for_review" />{" "}
          <Box component="span" sx={nowrap}>
            after <Token>{values.recovery_confirmation_minutes} minutes</Token>
          </Box>{" "}
          <Box component="span" sx={nowrap}>
            at{" "}
            <Token tip={explainLimit(values, usual, "recovery")}>
              {fmt(recover)} or fewer
            </Token>
          </Box>
          .
        </Typography>
        <Ruler escalate={escalate} recover={recover} usual={usual} />
      </Box>
      {stale && (
        <Typography
          typography="s2"
          color="error.main"
          component="p"
          sx={{ mt: 1.25 }}
        >
          Fix the highlighted values to update this preview.
        </Typography>
      )}
    </Box>
  );
}

EscalationSummary.propTypes = {
  values: PropTypes.object.isRequired,
  usual: PropTypes.number.isRequired,
  onUsualChange: PropTypes.func.isRequired,
  stale: PropTypes.bool,
  baselineHours: PropTypes.number,
};
