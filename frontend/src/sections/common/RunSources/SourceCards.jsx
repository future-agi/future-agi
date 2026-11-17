// Level 1 · `What are you testing?` (R4-SPEC D2; lane Ra MUST 1). Six equal
// cards from catalog.js (copy and counts computed there), at most one
// `Recommended`, the Connected now strip when an agent the user may use is
// online, and search over the whole catalog (results replace the cards).
import React, { useId, useMemo, useState } from "react";
import { CLASSIC_EXPERIMENT_COPY } from "src/launch/classicExperimentCopy";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import BundledIcon from "./BundledIcon";
import CustomTooltip from "src/components/tooltip";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import { fmtCount } from "src/sections/common/format/fmt";
import ConnectionPill from "src/sections/common/Connect/ConnectionPill";
import { usePromptExecutions } from "src/api/develop/prompt";
import { useGetListOfAgents } from "src/api/experiment/use-get-agents";
import { useGraphModels, withGraphModels } from "./graphModels-g2";
import {
  STEP1_COPY,
  moreCount,
  cardUnavailableReason,
  catalogEntry,
  orList,
  runSourceCard,
  searchCatalog,
  searchHeldEntries,
  searchStepCards,
  stepCard,
  stepCardsFor,
  stepDisambiguation,
  stepWayCount,
  stepWayNames,
  stepCardShown,
  tileUnavailableReason,
} from "./catalog";
import { LOADING_AVAILABILITY } from "./runSourceAvailability";
import SourceLogo from "./SourceLogo";
import CatalogTile from "./CatalogTile";

// L7c g2 (picker:search-no-match): a search with no match names every way
// step 1 offers that can be picked here (stepWayNames), and none when the
// server refused them all.
const noMatchText = (query, names) =>
  names.length
    ? `No match for "${query}". Try ${orList(names)}.`
    : `No match for "${query}".`;

// A search Enter whose hits are all greyed (held, or refused by the server)
// says so and names the ways that can be picked.
const noPickText = (names) =>
  names.length
    ? `None of these can be picked here. Try ${orList(names)}.`
    : "None of these can be picked here.";

function SourceCard({ card, keyNum, recommended, onPick, why, availability }) {
  const reasonId = useId();
  // The server's plain reason (Prompt, Agent graph); Already running is
  // greyed only when the server runs none of its kinds (or has not answered);
  // a held card (Your code, Agent harness, Voice agent) always carries
  // "Not available in this release".
  const unavailableReason = cardUnavailableReason(card, availability);
  const disabled = Boolean(unavailableReason);
  // options-truth L1 B: `+N` = every entry behind the card minus the logos
  // shown (custom entries counted), so 4 logos + `+4` = the 8 harnesses.
  const shownLogos = (card.logoIds ?? []).slice(0, 4);
  const moreN = moreCount(card, shownLogos.length);
  // IT-R2 G1 P5: compact (6 fit at 1280×720), the title never clipped;
  // `Recommended` sits on the logo row with the reason in its tooltip.
  return (
    <CustomTooltip
      show
      // IT-R4 finalize: each card says what happens next (was the Your
      // code sentence on all six). A held card says what it is, then the
      // release line.
      title={
        card.held
          ? `${card.line} ${unavailableReason}`
          : unavailableReason ??
            (keyNum ? `${card.tip}  ·  Press ${keyNum}` : card.tip)
      }
      size="small"
      placement="top"
      arrow
      enterDelay={600}
    >
      <Box
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled || undefined}
        // A greyed card is named by its title and described by its reason
        // (the tooltip would otherwise name it by its own text).
        aria-label={disabled ? card.title : undefined}
        aria-describedby={disabled ? reasonId : undefined}
        aria-keyshortcuts={!disabled && keyNum ? String(keyNum) : undefined}
        data-testid={`source-card-${card.id}`}
        onClick={disabled ? undefined : (e) => onPick(card.id, e.currentTarget)}
        onKeyDown={(e) => {
          if (disabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onPick(card.id, e.currentTarget);
          }
        }}
        sx={{
          position: "relative",
          border: "1px solid",
          // IT-R4 G1 P4: no card is highlighted except on hover / focus.
          borderColor: "border.light",
          borderRadius: 0.75,
          px: 1.25,
          py: 1,
          display: "flex",
          flexDirection: "column",
          gap: 0.5,
          cursor: disabled ? "not-allowed" : "pointer",
          bgcolor: disabled ? "background.neutral" : "background.paper",
          outline: "none",
          minWidth: 0,
          "& .sc-key": { opacity: 0, transition: "opacity 120ms" },
          ...(!disabled && {
            "&:hover, &:focus-visible": {
              borderColor: "purple.500",
              bgcolor: "purple.o5",
            },
            "&:hover .sc-key, &:focus-visible .sc-key": { opacity: 1 },
          }),
          "&:focus-visible": {
            boxShadow: (t) => `0 0 0 2px ${t.palette.primary.main}`,
          },
        }}
      >
        <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
          <Box
            sx={{
              width: 26,
              height: 26,
              borderRadius: 0.5,
              bgcolor: "background.neutral",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <BundledIcon
              icon={card.icon}
              width={18}
              sx={{ color: "text.primary" }}
            />
          </Box>
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            sx={{ minWidth: 0, lineHeight: 1.25 }}
          >
            {card.title}
          </Typography>
        </Stack>
        <Typography
          id={disabled ? reasonId : undefined}
          typography="s2"
          color="text.secondary"
          sx={{
            display: disabled ? "block" : "-webkit-box",
            WebkitLineClamp: disabled ? "unset" : 2,
            WebkitBoxOrient: "vertical",
            overflow: disabled ? "visible" : "hidden",
            minHeight: 36,
          }}
        >
          {unavailableReason ?? (why ? `Matches ${why}` : card.line)}
        </Typography>
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minHeight: 18, minWidth: 0 }}
        >
          {shownLogos.map((id) => (
            <SourceLogo key={id} entryId={id} size={16} tooltip />
          ))}
          {shownLogos.length && moreN ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid={`source-card-more-${card.id}`}
            >
              +{moreN}
            </Typography>
          ) : null}
          <Box sx={{ flex: 1 }} />
          {recommended && !disabled ? (
            <CustomTooltip
              show={Boolean(recommended.reason)}
              title={recommended.reason ?? ""}
              size="small"
              placement="top"
            >
              <Typography
                component="span"
                typography="s3"
                data-testid="recommended-badge"
                sx={{
                  px: 0.75,
                  borderRadius: 0.5,
                  bgcolor: "purple.o10",
                  color: "primary.main",
                  fontWeight: "fontWeightMedium",
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                }}
              >
                Recommended
              </Typography>
            </CustomTooltip>
          ) : null}
        </Stack>
        {keyNum && !disabled ? (
          <Box
            className="sc-key"
            sx={{ position: "absolute", top: 8, right: 8 }}
          >
            <KeyCap>{keyNum}</KeyCap>
          </Box>
        ) : null}
      </Box>
    </CustomTooltip>
  );
}
SourceCard.propTypes = {
  card: PropTypes.object.isRequired,
  keyNum: PropTypes.number,
  why: PropTypes.string,
  recommended: PropTypes.object,
  onPick: PropTypes.func.isRequired,
  availability: PropTypes.object,
};

/** The Connected now strip: the returning user's two-click path. */
export function ConnectedNowStrip({
  connected,
  rowCount,
  rowNoun,
  onRun,
  onDismiss,
}) {
  const { machine, agent } = connected;
  const entry = catalogEntry(agent.entry_id);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      data-testid="connected-now"
      sx={{
        px: 1.25,
        py: 0.75,
        border: "1px solid",
        borderColor: "success.light",
        borderRadius: 0.75,
        bgcolor: (t) =>
          t.palette.mode === "dark"
            ? "rgba(34,197,94,0.08)"
            : "rgba(34,197,94,0.06)",
        minWidth: 0,
      }}
    >
      <ConnectionPill machineId={machine.id} agentId={agent.id} size="small" />
      <SourceLogo entryId={agent.entry_id} size={16} />
      <Typography typography="s2" noWrap sx={{ minWidth: 0, flex: 1 }}>
        {`${agent.name}${entry ? ` (${entry.name})` : ""} is connected`}
      </Typography>
      <Button
        size="small"
        variant="contained"
        onClick={onRun}
        sx={{ flexShrink: 0 }}
      >
        {rowCount ? `Run it on these ${fmtCount(rowCount, rowNoun)}` : "Run it"}
      </Button>
      <Button
        size="small"
        onClick={onDismiss}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      >
        Not now
      </Button>
    </Stack>
  );
}
ConnectedNowStrip.propTypes = {
  connected: PropTypes.object.isRequired,
  rowCount: PropTypes.number,
  rowNoun: PropTypes.string,
  onRun: PropTypes.func.isRequired,
  onDismiss: PropTypes.func.isRequired,
};

/**
 * Level 1. `query` replaces the cards with catalog results; `Enter` in the
 * search picks the first.
 */
export default function SourceCards({
  datasetKind: _datasetKind,
  recommended = null,
  wide,
  query,
  onQuery,
  searchRef,
  onPickCard,
  onPickEntry,
  connected,
  connectedStrip,
  availability = LOADING_AVAILABILITY,
}) {
  // IT-R2 G1 P5: `Recommended` only from context (the dataset's baseline
  // run kind, or the run being re-tested); none otherwise.
  // IT-R4 G1 P4: cards in the order for the dataset kind; `Recommended`
  // on the first card only (the kind's reason as its tooltip).
  // options-truth L1 B: the door's `recommended` ({card, reason}) wins over
  // the dataset kind's first card; L7c g2: that card is shown first.
  // The server's report (`availability`) decides what can be picked; every
  // card and tile the launch shows stays on screen, greyed with its reason
  // when it cannot be picked here.
  const agentNoticeId = useId();
  const { cards, rec: kindRec } = stepCardsFor(
    _datasetKind,
    recommended,
    availability,
  );
  const rec = kindRec?.card ?? null;
  const n = stepWayCount(availability);
  const searching = Boolean(query?.trim());
  // L7c g2 (picker:search): prompts, graphs and their models are part of
  // the index (read once a search starts).
  const prompts = usePromptExecutions(searching, "", "chat");
  const graphs = useGetListOfAgents(searching, "", [], false);
  const graphList = useMemo(
    () =>
      (graphs.data?.pages ?? []).flatMap((p) => p?.graphs ?? p?.results ?? []),
    [graphs.data],
  );
  // The models each graph's versions call (the list carries none).
  const graphModels = useGraphModels(graphList, searching);
  const names = useMemo(
    () => ({
      prompts: (prompts.data?.pages ?? []).flatMap((p) => p?.results ?? []),
      graphs: withGraphModels(graphList, graphModels.data),
    }),
    [prompts.data, graphList, graphModels.data],
  );
  const cardHits = useMemo(
    () => (searching ? searchStepCards(query, names, availability) : []),
    [searching, query, names, availability],
  );
  // Every source the launch shows can match; a refused one is greyed, and a
  // held one (Traces I already have, Upload results) follows, greyed.
  const results = useMemo(
    () =>
      searching ? [...searchCatalog(query), ...searchHeldEntries(query)] : null,
    [searching, query],
  );
  const firstPickable = useMemo(
    () =>
      (results ?? []).findIndex((e) => !tileUnavailableReason(e, availability)),
    [results, availability],
  );
  // The first card hit that can be picked here. A held or refused card hit
  // is drawn greyed and skipped, so Enter reaches the first pickable tile.
  const pickableCardHit = useMemo(
    () =>
      cardHits.find((h) => {
        const card = runSourceCard(h.card, availability);
        return Boolean(card) && !cardUnavailableReason(card, availability);
      }) ?? null,
    [cardHits, availability],
  );
  // The query whose Enter found nothing to pick; the line shows only while
  // that query is in the box.
  const [missedQuery, setMissedQuery] = useState(null);
  const nothingPickable =
    searching &&
    missedQuery === query &&
    !pickableCardHit &&
    firstPickable === -1;
  const loadingNames =
    searching &&
    (prompts.isPending || graphs.isPending || graphModels.isLoading) &&
    !cardHits.length;
  const anyHit = cardHits.length > 0 || (results?.length ?? 0) > 0;
  return (
    <Stack spacing={1} data-testid="source-cards">
      <Box>
        {/* IT-R4 G1 P8: the definitions live in the heading's tooltip and
            on the cards (screen 07 ≤ 110 words at 1280×720). */}
        <CustomTooltip
          show
          title={stepDisambiguation(availability)}
          size="small"
          placement="top-start"
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            sx={{ display: "inline-block" }}
          >
            {STEP1_COPY.title}
          </Typography>
        </CustomTooltip>
      </Box>
      {connected ? connectedStrip : null}
      {!stepCardShown("agent") ? (
        <Stack spacing={0.5} alignItems="flex-start">
          <Button disabled variant="outlined" aria-describedby={agentNoticeId}>
            {CLASSIC_EXPERIMENT_COPY.agentLabel}
          </Button>
          <Typography id={agentNoticeId} typography="s2" color="text.secondary">
            {CLASSIC_EXPERIMENT_COPY.agentUnavailable}
          </Typography>
        </Stack>
      ) : null}
      <TextField
        inputRef={searchRef}
        size="small"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && anyHit) {
            e.preventDefault();
            // The first hit that can be picked: an offered card, else the
            // first tile the server runs. Held and refused hits are greyed
            // and never picked; with none left, a line says so.
            if (pickableCardHit) {
              onPickCard(pickableCardHit.card, e.currentTarget);
            } else if (firstPickable !== -1) {
              onPickEntry(results[firstPickable].id);
            } else {
              setMissedQuery(query);
            }
          }
          if (e.key === "Escape" && query) {
            e.stopPropagation();
            onQuery("");
          }
        }}
        placeholder={STEP1_COPY.searchPlaceholder(n, availability)}
        inputProps={{
          "aria-label": "Search ways to run",
          "data-testid": "source-search",
        }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="mdi:magnify"
                width={18}
                sx={{ color: "text.disabled" }}
              />
            </InputAdornment>
          ),
          endAdornment: (
            <InputAdornment position="end">
              <KeyCap>/</KeyCap>
            </InputAdornment>
          ),
        }}
        sx={{ "& .MuiOutlinedInput-root": { height: 34 } }}
      />
      {nothingPickable ? (
        <Typography
          role="status"
          typography="s2"
          color="text.secondary"
          data-testid="source-search-none-pickable"
        >
          {noPickText(stepWayNames(availability))}
        </Typography>
      ) : null}
      {results ? (
        anyHit || loadingNames ? (
          <Box
            data-testid="source-search-results"
            sx={{
              display: "grid",
              gridTemplateColumns: wide
                ? "repeat(3, minmax(0, 1fr))"
                : "repeat(2, minmax(0, 1fr))",
              gap: 1,
            }}
          >
            {cardHits.map((h) => (
              <Box key={h.card} data-testid={`search-card-${h.card}`}>
                <SourceCard
                  card={stepCard(h.card, availability)}
                  why={h.why}
                  recommended={null}
                  onPick={onPickCard}
                  availability={availability}
                />
              </Box>
            ))}
            {results.slice(0, 12).map((e, i) => (
              <CatalogTile
                key={e.id}
                entry={e}
                first={i === firstPickable && !pickableCardHit}
                availability={availability}
                onPick={() => onPickEntry(e.id)}
              />
            ))}
            {results.length > 12 ? (
              <Typography
                typography="s3"
                color="text.secondary"
                data-testid="source-search-truncated"
                sx={{ gridColumn: "1 / -1" }}
              >
                {STEP1_COPY.truncated(12, results.length)}
              </Typography>
            ) : null}
          </Box>
        ) : (
          <Stack spacing={1}>
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="source-no-match"
            >
              {noMatchText(query.trim(), stepWayNames(availability))}
            </Typography>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: wide
                  ? "repeat(3, minmax(0, 1fr))"
                  : "repeat(2, minmax(0, 1fr))",
                gap: 1,
              }}
            >
              {cards.map((card) => (
                <SourceCard
                  key={card.id}
                  card={card}
                  recommended={null}
                  onPick={onPickCard}
                  availability={availability}
                />
              ))}
            </Box>
          </Stack>
        )
      ) : (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: wide
              ? "repeat(3, minmax(0, 1fr))"
              : "repeat(2, minmax(0, 1fr))",
            gap: 1,
          }}
        >
          {cards.map((card, i) => (
            <SourceCard
              key={card.id}
              card={card}
              keyNum={i + 1}
              recommended={card.id === rec ? kindRec : null}
              onPick={onPickCard}
              availability={availability}
            />
          ))}
        </Box>
      )}
    </Stack>
  );
}

SourceCards.propTypes = {
  datasetKind: PropTypes.string,
  /** Additive (IT-R2 G1 P5): `{card, reason}` from context, or null. */
  recommended: PropTypes.object,
  wide: PropTypes.bool,
  query: PropTypes.string,
  onQuery: PropTypes.func.isRequired,
  searchRef: PropTypes.object,
  onPickCard: PropTypes.func.isRequired,
  onPickEntry: PropTypes.func.isRequired,
  connected: PropTypes.object,
  connectedStrip: PropTypes.node,
  /** The server's run-source report (useRunSourceAvailability). */
  availability: PropTypes.object,
};
