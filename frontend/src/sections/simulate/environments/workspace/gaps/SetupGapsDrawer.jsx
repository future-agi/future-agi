import PropTypes from "prop-types";
import { useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import EmptyState from "../../components/EmptyState";
import UiOnlyFlag from "../../components/UiOnlyFlag";

// Vel's "Needs input" drawer: what the builder couldn't work out from the
// agent. The API has no setup questions yet, so a real environment gets the
// empty state; ?uiSample=gaps fills it with Vel's sample (see
// _fixtures/detail/gaps.js). Answers only change that sample.

export const GAP_STATUS = {
  blocking: { label: "Blocking" },
  optional: { label: "Optional" },
  assumed: { label: "Assumed" },
  confirmed: { label: "Confirmed" },
  skipped: { label: "Skipped" },
};

const num = { fontVariantNumeric: "tabular-nums" };
const MONO = "ui-monospace, Menlo, monospace";
const waitingOnYou = (gap) =>
  gap.status === "blocking" || gap.status === "optional";

export function gapMeter(gaps) {
  return {
    blocking: gaps.filter((g) => g.status === "blocking").length,
    optional: gaps.filter((g) => g.status === "optional").length,
    resolved: gaps.filter((g) => !waitingOnYou(g)).length,
  };
}

// The settled stamp: who settled it, then with what.
const stampOf = (gap) => {
  if (gap.status === "assumed")
    return [
      gap.by === "builder" ? "Assumed by the builder" : "Assumed",
      gap.answered,
    ];
  if (gap.status === "confirmed")
    return ["Confirmed", gap.answered || "answered"];
  if (gap.status === "skipped") return ["Skipped", null];
  return gap.suggested ? ["Suggested", gap.suggested] : null;
};

export default function SetupGapsDrawer({
  gaps,
  credentials = [],
  suiteTotal = 0,
  sitOut = 0,
  onAnswer,
  onGo,
  onClose,
}) {
  const meter = gapMeter(gaps);
  const blocking = gaps.filter((g) => g.status === "blocking");
  const waiting = gaps.filter((g) => g.status === "optional");
  const settled = gaps.filter((g) => !waitingOnYou(g));
  const set = credentials.filter((c) => c.ref || c.satisfiedBy).length;
  const go = (to) => {
    onClose?.();
    onGo?.(to);
  };
  const cardProps = { onAnswer, onGo: go, suiteTotal };

  return (
    <Stack sx={{ height: "100%" }}>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={2}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s1", fontWeight: 600 }}>
              Needs input
            </Typography>
            <UiOnlyFlag feature="needsInput" />
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            What we couldn&apos;t work out from your agent. Blocking items hold
            a run; optional ones only cost coverage.
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <Iconify
            icon="solar:close-linear"
            width={20}
            sx={{ color: "text.secondary" }}
          />
        </IconButton>
      </Stack>

      <Box sx={{ flex: 1, overflowY: "auto" }}>
        <Stack spacing={2.5} sx={{ px: 2.5, py: 2.25 }}>
          {gaps.length === 0 ? (
            <EmptyState
              icon="solar:inbox-linear"
              title="Nothing to resolve"
              body="Setup questions aren't in the API yet. When they are, what the builder couldn't work out from your agent shows here."
            />
          ) : (
            <>
              <Figures meter={meter} suiteTotal={suiteTotal} sitOut={sitOut} />
              <GapGroup
                label="Blocking"
                note="A run can't start until these are answered"
                gaps={blocking}
                {...cardProps}
              />
              <GapGroup
                label="Waiting on you"
                note="A run can start; what rests on these sits out"
                gaps={waiting}
                {...cardProps}
              />
            </>
          )}

          {credentials.length > 0 && (
            <Part
              label="Credentials"
              count={`${set} of ${credentials.length} set`}
              note="Values stay in your secret manager"
            >
              <CredentialsList credentials={credentials} onAnswer={onAnswer} />
            </Part>
          )}

          <GapGroup
            label="Settled"
            note="Answered, assumed or skipped. Change any."
            gaps={settled}
            collapsed
            {...cardProps}
          />
        </Stack>
      </Box>
    </Stack>
  );
}

SetupGapsDrawer.propTypes = {
  gaps: PropTypes.array.isRequired,
  credentials: PropTypes.array,
  suiteTotal: PropTypes.number,
  sitOut: PropTypes.number,
  onAnswer: PropTypes.func,
  onGo: PropTypes.func,
  onClose: PropTypes.func,
};

// Four counts across the top, red only when something blocks the run.
function Figures({ meter, suiteTotal, sitOut }) {
  const cells = [
    {
      k: "Blocking",
      v: meter.blocking,
      sub: meter.blocking ? "Hold the run" : "Run can start",
      red: meter.blocking > 0,
    },
    {
      k: "Optional",
      v: meter.optional,
      sub: meter.optional ? "Scenarios may sit out" : "None open",
    },
    { k: "Resolved", v: meter.resolved, sub: "Settled" },
    {
      k: "Sit out",
      v: meter.blocking ? "-" : sitOut,
      sub: meter.blocking
        ? "Run held"
        : sitOut
          ? `of ${suiteTotal} scenarios`
          : "Every scenario runs",
    },
  ];
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        "& > * + *": { borderLeft: "1px solid", borderColor: "divider" },
      }}
    >
      {cells.map((c) => (
        <Box key={c.k} sx={{ px: 1.75, py: 1.25, minWidth: 0 }}>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {c.k}
          </Typography>
          <Typography
            sx={{
              typography: "m3",
              fontWeight: 600,
              lineHeight: 1.35,
              mt: 0.25,
              color: c.red ? "error.main" : "text.primary",
              ...num,
            }}
          >
            {c.v}
          </Typography>
          <Typography
            noWrap
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
          >
            {c.sub}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
Figures.propTypes = {
  meter: PropTypes.object,
  suiteTotal: PropTypes.number,
  sitOut: PropTypes.number,
};

// A titled, ruled sheet.
function Part({ label, count, note, open = true, onToggle, children }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        role={onToggle ? "button" : undefined}
        tabIndex={onToggle ? 0 : undefined}
        aria-expanded={onToggle ? open : undefined}
        onClick={onToggle}
        onKeyDown={(e) =>
          onToggle && (e.key === "Enter" || e.key === " ") && onToggle()
        }
        sx={{ mb: 1, ...(onToggle && { cursor: "pointer" }) }}
      >
        <Typography
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: "text.subtitle",
            whiteSpace: "nowrap",
            ...num,
          }}
        >
          {label}
          {count != null && (
            <Box
              component="span"
              sx={{ fontWeight: 600, ml: 0.75 }}
            >{`· ${count}`}</Box>
          )}
        </Typography>
        {note && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              color: "text.subtitle",
              flex: 1,
              minWidth: 0,
              textAlign: "right",
            }}
          >
            {note}
          </Typography>
        )}
        {onToggle && (
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={14}
            sx={{ color: "text.subtitle", flexShrink: 0, alignSelf: "center" }}
          />
        )}
      </Stack>
      {open && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
Part.propTypes = {
  label: PropTypes.string,
  count: PropTypes.node,
  note: PropTypes.string,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  children: PropTypes.node,
};

function GapGroup({ label, note, gaps, collapsed = false, ...cardProps }) {
  const [open, setOpen] = useState(!collapsed);
  if (!gaps.length) return null;
  return (
    <Part
      label={label}
      count={gaps.length}
      note={note}
      open={open}
      onToggle={collapsed ? () => setOpen((o) => !o) : undefined}
    >
      {gaps.map((g, i) => (
        <GapCard
          key={g.id}
          gap={g}
          defaultOpen={i === 0 && waitingOnYou(g)}
          {...cardProps}
        />
      ))}
    </Part>
  );
}
GapGroup.propTypes = {
  label: PropTypes.string,
  note: PropTypes.string,
  gaps: PropTypes.array,
  collapsed: PropTypes.bool,
};

// One question: what it is, why it matters, and the ways out.
function GapCard({ gap, onAnswer, onGo, suiteTotal, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const [providing, setProviding] = useState(false);
  const [choice, setChoice] = useState(
    gap.answered || gap.suggested || gap.options?.[0] || "",
  );
  const [text, setText] = useState("");
  const waiting = waitingOnYou(gap);
  const stamp = stampOf(gap);
  const n = gap.coverage || 0;
  const pct = suiteTotal ? Math.round((n / suiteTotal) * 100) : 0;
  const what = gap.coverageWhat || "scenarios";
  const answer = (status, value) => {
    onAnswer?.(gap.id, status, value);
    setProviding(false);
  };
  const subline = waiting
    ? `${GAP_STATUS[gap.status]?.label}${!open && n ? ` · ${n} scenario${n === 1 ? "" : "s"} rest on it` : ""}`
    : `${stamp?.[0] ?? ""}${!open && stamp?.[1] ? ` · ${stamp[1]}` : ""}`;

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) =>
          (e.key === "Enter" || e.key === " ") && setOpen((o) => !o)
        }
        sx={{
          px: 1.75,
          py: 1.25,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <StatusMark status={gap.status} size={20} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {gap.title}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {subline}
          </Typography>
        </Box>
        <Iconify
          icon={
            open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
          }
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack
          spacing={1.25}
          sx={{ pr: 1.75, pb: 1.75, pl: "calc(14px + 32px)" }}
        >
          <Typography
            sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
          >
            {gap.why}
          </Typography>

          {(stamp ||
            (n > 0 &&
              (gap.status === "optional" || gap.status === "skipped"))) && (
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "92px minmax(0, 1fr)",
                columnGap: 1.5,
                rowGap: 0.5,
              }}
            >
              {stamp && (
                <>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      lineHeight: 1.6,
                    }}
                  >
                    {stamp[0]}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.primary",
                      fontWeight: 600,
                      lineHeight: 1.6,
                    }}
                  >
                    {stamp[1] ?? "-"}
                  </Typography>
                </>
              )}
              {n > 0 &&
                (gap.status === "optional" || gap.status === "skipped") && (
                  <>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.subtitle",
                        lineHeight: 1.6,
                      }}
                    >
                      {gap.status === "skipped" ? "Sitting out" : "If skipped"}
                    </Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        lineHeight: 1.6,
                        ...num,
                      }}
                    >
                      {`${n} ${what} (${pct}% of the suite) ${n === 1 ? "sits" : "sit"} out${gap.status === "skipped" ? "" : " the run"} until this is answered`}
                    </Typography>
                  </>
                )}
            </Box>
          )}

          <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
            {gap.status === "optional" && gap.suggested && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={() => answer("assumed", gap.suggested)}
              >
                Accept suggestion
              </Button>
            )}
            {gap.status === "assumed" && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => answer("confirmed", gap.answered)}
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                Confirm
              </Button>
            )}
            {(gap.options?.length > 1 || gap.input) && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => setProviding((p) => !p)}
                sx={{ color: "text.primary", borderColor: "divider" }}
              >
                {gap.status === "optional" ? "Provide value" : "Change"}
              </Button>
            )}
            {gap.status === "optional" && (
              <Button
                size="small"
                onClick={() => answer("skipped")}
                sx={{ color: "text.subtitle" }}
              >
                Skip
              </Button>
            )}
            {gap.by === "you" && (
              <Button
                size="small"
                onClick={() => answer("reopen")}
                sx={{ color: "text.subtitle" }}
              >
                Reopen
              </Button>
            )}
            {gap.ask && (
              <Button
                size="small"
                onClick={() => onGo?.(gap.ask.to)}
                endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
                sx={{ color: "primary.main" }}
              >
                {gap.ask.label}
              </Button>
            )}
          </Stack>

          <Collapse in={providing} unmountOnExit>
            <Stack spacing={1} sx={{ pt: 0.5 }}>
              {gap.options?.length > 1 && (
                <Stack
                  role="radiogroup"
                  aria-label={gap.title}
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    overflow: "hidden",
                  }}
                  divider={
                    <Box
                      sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                    />
                  }
                >
                  {gap.options.map((o) => {
                    const sel = !text && choice === o;
                    const pick = () => {
                      setChoice(o);
                      setText("");
                    };
                    return (
                      <Stack
                        key={o}
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        role="radio"
                        aria-checked={sel}
                        tabIndex={0}
                        onClick={pick}
                        onKeyDown={(e) =>
                          (e.key === "Enter" || e.key === " ") && pick()
                        }
                        sx={{
                          px: 1.25,
                          py: 0.75,
                          cursor: "pointer",
                          bgcolor: sel ? "action.selected" : "transparent",
                          "&:hover": {
                            bgcolor: sel ? "action.selected" : "action.hover",
                          },
                        }}
                      >
                        <Iconify
                          icon={
                            sel
                              ? "solar:check-circle-bold"
                              : "solar:circle-linear"
                          }
                          width={14}
                          sx={{
                            color: sel ? "text.primary" : "text.disabled",
                            flexShrink: 0,
                          }}
                        />
                        <Typography
                          sx={{
                            typography: "s2",
                            fontWeight: sel ? 600 : 400,
                            flex: 1,
                          }}
                        >
                          {o}
                        </Typography>
                        {o === gap.suggested && (
                          <Typography
                            sx={{ typography: "s3", color: "text.disabled" }}
                          >
                            default
                          </Typography>
                        )}
                      </Stack>
                    );
                  })}
                </Stack>
              )}
              {gap.input && (
                <TextField
                  size="small"
                  fullWidth
                  label={gap.input.label}
                  placeholder={gap.input.placeholder}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              )}
              <Box>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!(text.trim() || choice)}
                  onClick={() => answer("confirmed", text.trim() || choice)}
                >
                  Save answer
                </Button>
              </Box>
            </Stack>
          </Collapse>
        </Stack>
      </Collapse>
    </Box>
  );
}
GapCard.propTypes = {
  gap: PropTypes.object.isRequired,
  onAnswer: PropTypes.func,
  onGo: PropTypes.func,
  suiteTotal: PropTypes.number,
  defaultOpen: PropTypes.bool,
};

// A soft tinted circle with a light glyph: red only for blocking.
function StatusMark({ status, size = 22 }) {
  const theme = useTheme();
  const tone =
    status === "blocking"
      ? theme.palette.error.main
      : theme.palette.text.secondary;
  const w = Math.round(size * 0.5);
  const common = {
    width: w,
    height: w,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: tone,
    strokeWidth: 2.5,
    strokeLinecap: "round",
    strokeLinejoin: "round",
  };
  const glyph = (() => {
    if (status === "confirmed")
      return <polyline points="5,12.5 10,17.5 19,7" />;
    if (status === "blocking")
      return (
        <>
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="18" y1="6" x2="6" y2="18" />
        </>
      );
    if (status === "skipped") return <line x1="7" y1="12" x2="17" y2="12" />;
    if (status === "assumed")
      return (
        <>
          <circle cx="8" cy="12" r="1.3" fill={tone} stroke="none" />
          <circle cx="12" cy="12" r="1.3" fill={tone} stroke="none" />
          <circle cx="16" cy="12" r="1.3" fill={tone} stroke="none" />
        </>
      );
    return (
      <>
        <circle cx="12" cy="7.5" r="1.5" fill={tone} stroke="none" />
        <line x1="12" y1="11" x2="12" y2="17" />
      </>
    );
  })();
  return (
    <Box
      role="img"
      aria-label={(GAP_STATUS[status] || GAP_STATUS.optional).label}
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        display: "grid",
        placeItems: "center",
        bgcolor: alpha(tone, 0.12),
        border: "1px solid",
        borderColor: alpha(tone, 0.24),
      }}
    >
      <Box component="svg" {...common} sx={{ display: "block" }}>
        {glyph}
      </Box>
    </Box>
  );
}
StatusMark.propTypes = { status: PropTypes.string, size: PropTypes.number };

// The credentials the source needs, as a manifest. Values never appear: a
// requirement carries a reference into the team's secret manager.
function CredentialsList({ credentials, onAnswer }) {
  return credentials.map((c) => {
    const managed = !!c.satisfiedBy;
    return (
      <Box key={c.id} sx={{ px: 1.75, py: 1.125 }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) auto",
            columnGap: 2,
            alignItems: "start",
          }}
        >
          <Box minWidth={0}>
            <Typography
              noWrap
              sx={{
                typography: "s2",
                fontWeight: 600,
                fontFamily: MONO,
                fontSize: 13,
              }}
            >
              {c.id}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.subtitle" }}
            >
              {`${c.purpose} · from ${c.detectedFrom}`}
            </Typography>
          </Box>
          {!managed && (
            <Button
              size="small"
              onClick={() => onAnswer?.(c.id, "credential")}
              sx={{
                color: "text.primary",
                fontWeight: 600,
                minWidth: 0,
                py: 0,
              }}
            >
              {c.ref ? "Change" : "Set reference"}
            </Button>
          )}
        </Box>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            mt: 0.5,
            color: managed || c.ref ? "text.secondary" : "text.subtitle",
            ...(c.ref && { fontFamily: MONO }),
          }}
        >
          {managed ? `Using ${c.satisfiedBy}` : c.ref || "Not set"}
        </Typography>
      </Box>
    );
  });
}
CredentialsList.propTypes = {
  credentials: PropTypes.array,
  onAnswer: PropTypes.func,
};
