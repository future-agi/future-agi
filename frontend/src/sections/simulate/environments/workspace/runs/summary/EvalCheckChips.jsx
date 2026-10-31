import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Menu, MenuItem, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";

const NUM = { fontVariantNumeric: "tabular-nums" };
const SHOWN = 4;
const DASH = "-";

const signed = (n) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)}`;
const round = (v) => (v == null ? null : Math.round(v));

// Each eval's latest score and how it moved since the run before, lowest
// first, from the eval columns the runs table shows.
export function rankEvals(evals, latest, previous) {
  return evals
    .map((e) => {
      const now = round(latest?.scores?.[e.id]);
      const before = round(previous?.scores?.[e.id]);
      return {
        ...e,
        now,
        d: now != null && before != null ? now - before : null,
      };
    })
    .sort((a, b) => (a.now ?? 101) - (b.now ?? 101));
}

// The four lowest evals as compact toggles; on, one draws its line over the
// pass rate. "More evals" lists the rest, so any eval can be drawn.
export default function EvalCheckChips({
  evals,
  latest,
  previous,
  focusId,
  onFocus,
}) {
  const [menuAnchor, setMenuAnchor] = useState(null);
  const rows = rankEvals(evals, latest, previous);
  const shown = rows.slice(0, SHOWN);
  const rest = rows.slice(SHOWN);
  const restFocused = rest.find((e) => e.id === focusId) || null;
  const toggle = (id) => onFocus(focusId === id ? null : id);

  return (
    <Stack
      direction="row"
      useFlexGap
      spacing={0.75}
      sx={{ flex: "0 1 auto", minWidth: 0, flexWrap: "wrap" }}
    >
      {shown.map((e) => {
        const on = focusId === e.id;
        const moved =
          e.d != null && previous
            ? `, ${signed(e.d)} vs ${previous.label}`
            : "";
        return (
          <CustomTooltip
            key={e.id}
            show
            arrow
            describeChild
            size="small"
            title={`${e.name} in ${latest?.label ?? "the latest run"}${moved}. ${on ? "Click to take it off the chart." : "Click to draw it on the chart."}`}
          >
            <Box
              role="button"
              tabIndex={0}
              aria-pressed={on}
              onClick={() => toggle(e.id)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  toggle(e.id);
                }
              }}
              sx={chipSx(on)}
            >
              {on && <Dot color={e.color} />}
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
              >
                {e.name}
              </Typography>
              <Score value={e.now} />
              {e.d ? (
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    flexShrink: 0,
                    ...NUM,
                  }}
                >
                  {signed(e.d)}
                </Typography>
              ) : null}
            </Box>
          </CustomTooltip>
        );
      })}
      {rest.length > 0 && (
        <>
          <Box
            component="button"
            type="button"
            aria-haspopup="menu"
            aria-expanded={!!menuAnchor}
            onClick={(ev) => setMenuAnchor(ev.currentTarget)}
            sx={{
              ...chipSx(!!restFocused),
              font: "inherit",
              color: "inherit",
            }}
          >
            {restFocused && <Dot color={restFocused.color} />}
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`More evals (${rest.length})`}
            </Typography>
          </Box>
          <Menu
            anchorEl={menuAnchor}
            open={!!menuAnchor}
            onClose={() => setMenuAnchor(null)}
            anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
            transformOrigin={{ vertical: "top", horizontal: "right" }}
            slotProps={{ paper: { sx: { maxHeight: 320, minWidth: 220 } } }}
          >
            {rest.map((e) => (
              <MenuItem
                key={e.id}
                selected={focusId === e.id}
                onClick={() => {
                  toggle(e.id);
                  setMenuAnchor(null);
                }}
                sx={{ gap: 0.75, py: 0.75 }}
              >
                <Dot color={e.color} />
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {e.name}
                </Typography>
                <Score value={e.now} />
              </MenuItem>
            ))}
          </Menu>
        </>
      )}
    </Stack>
  );
}

const evalShape = PropTypes.shape({
  id: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  color: PropTypes.string,
});

EvalCheckChips.propTypes = {
  evals: PropTypes.arrayOf(evalShape).isRequired,
  // Summary rows of the latest finished run and the one before it.
  latest: PropTypes.object,
  previous: PropTypes.object,
  focusId: PropTypes.string,
  onFocus: PropTypes.func.isRequired,
};

function chipSx(on) {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 0.75,
    height: 28,
    px: 1.25,
    borderRadius: "4px",
    cursor: "pointer",
    maxWidth: 220,
    border: "1px solid",
    borderColor: on ? "text.disabled" : "divider",
    bgcolor: on ? "action.hover" : "transparent",
    "&:hover": { borderColor: "text.disabled" },
  };
}

function Dot({ color }) {
  return (
    <Box
      sx={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        bgcolor: color,
        flexShrink: 0,
      }}
    />
  );
}

Dot.propTypes = { color: PropTypes.string };

function Score({ value }) {
  return (
    <Typography
      sx={{
        typography: "s3",
        fontWeight: "fontWeightSemiBold",
        color: "text.primary",
        flexShrink: 0,
        ...NUM,
      }}
    >
      {value == null ? DASH : `${value}%`}
    </Typography>
  );
}

Score.propTypes = { value: PropTypes.number };
