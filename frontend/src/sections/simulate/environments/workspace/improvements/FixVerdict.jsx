import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { checksLine, headline, verdictCards, winnerOf } from "./fixRunView";

const num = { fontVariantNumeric: "tabular-nums" };
const line = { typography: "s3", color: "text.subtitle" };

function Card({ card }) {
  const value =
    card.id === "gap" ? `${card.value} pts` : `${card.from}% → ${card.to}%`;
  return (
    <Box sx={{ px: 2, py: 0.875, minWidth: 0 }}>
      <Typography noWrap sx={line}>
        {card.label}
      </Typography>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography noWrap sx={{ typography: "s1", fontWeight: 600, ...num }}>
          {value}
        </Typography>
        {!!card.delta && (
          <Typography
            noWrap
            sx={{
              typography: "s3",
              fontWeight: 600,
              ...num,
              color: card.delta > 0 ? "status.success.fg" : "status.danger.fg",
            }}
          >
            {`${card.delta > 0 ? "▲" : "▼"} ${Math.abs(card.delta)} pts`}
          </Typography>
        )}
      </Stack>
      <Typography noWrap sx={line}>
        {card.note}
      </Typography>
    </Box>
  );
}
Card.propTypes = { card: PropTypes.object.isRequired };

const tierIcon = (status) =>
  ({
    passed: "solar:check-circle-linear",
    failed: "solar:close-circle-bold",
    pending: "solar:clock-circle-linear",
    skipped: "solar:minus-circle-linear",
  })[status] || "solar:question-circle-linear";

function Checks({ tiers }) {
  const [all, setAll] = useState(false);
  const failed = tiers.filter((t) => t.status === "failed");
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, py: 0.75, minWidth: 0 }}
      >
        <Iconify
          icon={
            failed.length
              ? "solar:close-circle-bold"
              : "solar:check-circle-linear"
          }
          width={14}
          sx={{
            color: failed.length ? "status.danger.fg" : "text.secondary",
            flexShrink: 0,
          }}
        />
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: 600, flexShrink: 0 }}
        >
          {checksLine(tiers)}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s2",
            color: "status.danger.fg",
            flex: 1,
            minWidth: 0,
          }}
        >
          {failed.map((t) => t.name).join(" · ")}
        </Typography>
        <Button
          size="small"
          onClick={() => setAll((o) => !o)}
          endIcon={
            <Iconify
              icon={
                all
                  ? "solar:alt-arrow-up-linear"
                  : "solar:alt-arrow-down-linear"
              }
              width={12}
            />
          }
          sx={{ color: "text.secondary", flexShrink: 0, minWidth: 0 }}
        >
          {all ? "Hide" : "Show all"}
        </Button>
      </Stack>
      {all && (
        <Box
          sx={{
            px: 2,
            pb: 1,
            pl: 4.25,
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "repeat(2, minmax(0, 1fr))" },
            columnGap: 3,
            rowGap: 0.5,
          }}
        >
          {tiers.map((t) => (
            <Stack
              key={t.name}
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ minWidth: 0 }}
            >
              <Iconify
                icon={tierIcon(t.status)}
                width={14}
                sx={{
                  color:
                    t.status === "failed"
                      ? "status.danger.fg"
                      : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                {t.name}
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
              >
                {t.detail}
              </Typography>
            </Stack>
          ))}
        </Box>
      )}
    </Box>
  );
}
Checks.propTypes = { tiers: PropTypes.array.isRequired };

function MustPassBreaks({ verdict }) {
  const counted = verdict.must_pass_regressions || [];
  const noise = verdict.must_pass_within_noise || [];
  if (!counted.length && !noise.length) return null;
  return (
    <Box
      data-testid="must-pass-breaks"
      sx={{ px: 2, py: 0.75, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        Must-pass breaks
      </Typography>
      {counted.map((key) => (
        <Typography
          key={key}
          sx={{ typography: "s2", color: "status.danger.fg" }}
        >
          {key}
        </Typography>
      ))}
      {noise.map((key) => (
        <Typography
          key={key}
          sx={{ typography: "s2", color: "text.secondary" }}
        >
          {`${key} · within normal noise, not counted`}
        </Typography>
      ))}
    </Box>
  );
}
MustPassBreaks.propTypes = { verdict: PropTypes.object.isRequired };

// The design's verdict block: the headline, the cards side by side, the checks
// on one line, then any must-pass breaks.
export default function FixVerdict({ fix }) {
  const winner = winnerOf(fix);
  const text = headline(fix);
  if (!winner || !text) return null;
  const cards = verdictCards(fix);
  const provenOnTargets = !winner.verdict.held_back_pass_rate;
  return (
    <Box
      sx={{
        mb: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Typography
        sx={{
          px: 2,
          py: 1,
          typography: "s1",
          fontWeight: 600,
          lineHeight: 1.45,
        }}
      >
        {text}
      </Typography>
      {provenOnTargets && (
        <Typography
          sx={{
            px: 2,
            mt: -0.75,
            pb: 1,
            typography: "s2",
            color: "text.secondary",
          }}
        >
          Proven on the targets only
        </Typography>
      )}
      <Box
        data-testid="verdict-cards"
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2, minmax(0, 1fr))",
            md: `repeat(${cards.length}, minmax(0, 1fr))`,
          },
          borderTop: "1px solid",
          borderColor: "divider",
          "& > *": {
            borderColor: "divider",
            borderStyle: "solid",
            borderWidth: 0,
          },
          "& > *:not(:first-of-type)": { borderLeftWidth: 1 },
        }}
      >
        {cards.map((card) => (
          <Card key={card.id} card={card} />
        ))}
      </Box>
      <Checks tiers={winner.verdict.tiers} />
      <MustPassBreaks verdict={winner.verdict} />
    </Box>
  );
}

FixVerdict.propTypes = { fix: PropTypes.object.isRequired };
