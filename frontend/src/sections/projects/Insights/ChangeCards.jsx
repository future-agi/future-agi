import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { buildChangeCards } from "./insightsChanges";
import { formatCount, formatWhen } from "./insightsFormat";
import { useReport } from "./reportContext";
import { Swatch, toneColor } from "./parts";

const VISIBLE = 6;
const QUIET_VISIBLE = 4;

const ARROW = {
  up: "mdi:arrow-up",
  down: "mdi:arrow-down",
  flat: "mdi:minus",
};

function ChangeCard({ card }) {
  const theme = useTheme();
  const report = useReport();
  const strong = card.significant;
  const category =
    card.categoryId && report.categoriesById.get(card.categoryId);
  const name = card.name || category?.name || "A new category";
  const accent = !strong
    ? theme.palette.text.disabled
    : card.kind === "metric"
      ? toneColor(theme, card.tone)
      : card.kind === "new"
        ? theme.palette.info.main
        : theme.palette.text.primary;
  const clickable = Boolean(category);
  const body = (
    <Box
      component={clickable ? "button" : "div"}
      type={clickable ? "button" : undefined}
      onClick={clickable ? () => report.openCategory(category.id) : undefined}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        width: "100%",
        minWidth: 0,
        px: 1.25,
        py: 0.875,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        font: "inherit",
        textAlign: "left",
        color: strong ? "text.primary" : "text.secondary",
        cursor: clickable ? "pointer" : "default",
        "&:hover": clickable ? { borderColor: "text.disabled" } : undefined,
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: 1,
        },
      }}
    >
      {card.kind === "new" ? (
        <Box
          component="span"
          sx={{
            px: 0.5,
            borderRadius: 0.5,
            fontSize: 11,
            fontWeight: 600,
            lineHeight: "16px",
            color: "info.dark",
            bgcolor: "info.lighter",
            flexShrink: 0,
          }}
        >
          New
        </Box>
      ) : (
        <Iconify
          icon={ARROW[card.direction]}
          width={14}
          sx={{ color: accent, flexShrink: 0 }}
        />
      )}
      {category && <Swatch color={report.colorOf(category.id)} size={8} />}
      <Typography
        variant="body2"
        component="span"
        noWrap
        sx={{
          fontSize: 13,
          fontWeight: strong ? 500 : 400,
          minWidth: 0,
          flex: 1,
        }}
      >
        {name}
      </Typography>
      <Typography
        variant="body2"
        component="span"
        sx={{
          fontSize: 13,
          flexShrink: 0,
          fontVariantNumeric: "tabular-nums",
          color: "text.secondary",
        }}
      >
        {card.kind === "new"
          ? Number.isFinite(card.count)
            ? `· ${formatCount(card.count)}${card.to ? ` (${card.to})` : ""}`
            : ""
          : `${card.from} → ${card.to}`}
        {card.kind !== "new" && card.deltaText && (
          <Box
            component="span"
            sx={{
              ml: 0.75,
              color: strong ? accent : "text.secondary",
              fontWeight: strong ? 600 : 400,
            }}
          >
            {card.deltaText}
          </Box>
        )}
      </Typography>
    </Box>
  );
  return (
    <Tooltip
      arrow
      describeChild
      title={
        strong
          ? card.kind === "new"
            ? "A category that was not in the previous run"
            : "A real change: it holds after correcting for every comparison"
          : "Within the noise for this sample size"
      }
    >
      {body}
    </Tooltip>
  );
}

ChangeCard.propTypes = { card: PropTypes.object.isRequired };

/** "What changed since the last run", as compact cards. */
export default function ChangeCards({ changes }) {
  const report = useReport();
  const [all, setAll] = useState(false);
  // A sub folded into its same-named top moved with it: one card, not two.
  const cards = buildChangeCards(
    changes,
    (id) => report.categoriesById?.get(id)?.parent_id,
  ).filter((card) => !report.echoes?.has(card.categoryId));
  if (!cards.length) return null;
  const strong = cards.filter((card) => card.significant).length;
  // With nothing beyond the noise, the largest moves are enough.
  const visible = strong ? Math.max(VISIBLE, strong) : QUIET_VISIBLE;
  const shown = all ? cards : cards.slice(0, visible);
  const quiet = cards.length - strong;
  return (
    <Box component="section" aria-labelledby="insights-changes">
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ mb: 1.25 }}
      >
        <Typography
          id="insights-changes"
          variant="subtitle1"
          component="h2"
          sx={{ fontSize: 15 }}
        >
          What changed since the last run
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {[
            changes.previous_finished_at &&
              `vs ${formatWhen(changes.previous_finished_at)}`,
            !strong && "nothing moved beyond the noise",
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      </Stack>
      <Box
        sx={{
          display: "grid",
          gap: 1,
          gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))",
        }}
      >
        {shown.map((card) => (
          <ChangeCard key={card.key} card={card} />
        ))}
      </Box>
      {cards.length > visible && (
        <Button
          size="small"
          color="inherit"
          onClick={() => setAll((value) => !value)}
          sx={{ mt: 0.5, fontWeight: 500, color: "text.secondary" }}
        >
          {all ? "Show fewer" : `Show ${cards.length - visible} more`}
        </Button>
      )}
      {quiet > 0 && strong > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ mt: 1, mb: 0 }}
        >
          Muted changes are within the noise for this sample size.
        </Typography>
      )}
    </Box>
  );
}

ChangeCards.propTypes = { changes: PropTypes.object };
