import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * A scenario paused on a clarifying question (PRD v6 · AC-15.13).
 *
 * Inline in the live view, above the three columns, because it is the one
 * thing on the screen that needs the reader: the harness can't go on with
 * this scenario until someone says what it should assume. The rest of the
 * run carries on underneath — said, so nobody thinks the whole run stopped.
 */
export default function ClarifyCard({ blocked, scenario, onAnswer, onOpen }) {
  const [pick, setPick] = useState(null);
  const [text, setText] = useState("");
  const [since, setSince] = useState(0);

  useEffect(() => {
    setPick(null);
    setText("");
  }, [blocked?.taskId, blocked?.askedAt]);

  /* How long it has waited — a held scenario that nobody answers is the
     thing to notice on a long run. */
  useEffect(() => {
    if (!blocked?.askedAt) return undefined;
    const tick = () =>
      setSince(Math.max(0, Math.round((Date.now() - blocked.askedAt) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [blocked?.askedAt]);

  if (!blocked) return null;
  const answer = text.trim() || pick;
  const name = scenario?.name || scenario?.title || "A scenario";

  return (
    <Box
      sx={{
        px: 3,
        py: 1.25,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <Box
        role="region"
        aria-label="Clarifying question"
        sx={{
          /* A neutral panel: the run's amber "Needs input" says it's
             waiting; colour here would only repeat it louder. */
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          bgcolor: "background.paper",
          px: 2,
          py: 1.5,
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <Box
            sx={{
              width: 28,
              height: 28,
              borderRadius: "4px",
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              color: "status.warning.fg",
            }}
          >
            <Iconify icon="solar:chat-round-dots-linear" width={16} />
          </Box>

          <Box flex={1} minWidth={0}>
            <Stack
              direction="row"
              alignItems="baseline"
              spacing={1}
              flexWrap="wrap"
              rowGap={0.25}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  textTransform: "none",
                  letterSpacing: 0.4,
                }}
              >
                Paused · needs your answer
              </Typography>
              <Typography
                role="button"
                tabIndex={0}
                onClick={onOpen}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onOpen?.();
                }}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.secondary",
                  cursor: "pointer",
                  textDecoration: "underline",
                  textDecorationColor: (t) =>
                    alpha(t.palette.text.primary, 0.3),
                  textUnderlineOffset: "3px",
                  "&:hover": { color: "text.primary" },
                }}
              >
                {name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {`· waiting ${formatWait(since)} · the other scenarios keep running`}
              </Typography>
            </Stack>

            <Typography sx={{ typography: "s1", fontWeight: 600, mt: 0.5 }}>
              {blocked.question}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {`${blocked.why} The scenario holds at turn ${blocked.step + 1} rather than guess — a guess would grade the agent on our assumption.`}
            </Typography>

            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              flexWrap="wrap"
              rowGap={0.75}
              sx={{ mt: 1.25 }}
            >
              {blocked.options.map((o) => {
                const on = pick === o && !text.trim();
                return (
                  <Button
                    key={o}
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      setPick(o);
                      setText("");
                    }}
                    startIcon={
                      on ? (
                        <Iconify icon="solar:check-circle-linear" width={14} />
                      ) : null
                    }
                    sx={{
                      color: "text.primary",
                      bgcolor: "background.paper",
                      borderColor: on ? "text.primary" : "divider",
                      "&:hover": {
                        borderColor: "text.disabled",
                        bgcolor: "background.paper",
                      },
                    }}
                  >
                    {o}
                  </Button>
                );
              })}
              <TextField
                size="small"
                placeholder="Or type the answer"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && answer) onAnswer(answer);
                }}
                InputProps={{
                  sx: {
                    typography: "s2",
                    height: 30,
                    bgcolor: "background.paper",
                  },
                }}
                sx={{ width: 240 }}
              />
              <Button
                size="small"
                variant="contained"
                color="primary"
                disabled={!answer}
                onClick={() => onAnswer(answer)}
                startIcon={<Iconify icon="solar:play-linear" width={14} />}
              >
                Answer and resume
              </Button>
            </Stack>
          </Box>
        </Stack>
      </Box>
    </Box>
  );
}

function formatWait(s) {
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

ClarifyCard.propTypes = {
  blocked: PropTypes.object,
  scenario: PropTypes.object,
  onAnswer: PropTypes.func,
  onOpen: PropTypes.func,
};
