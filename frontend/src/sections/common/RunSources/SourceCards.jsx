// Level 1 · `What are you testing?` (R4-SPEC D2; lane Ra MUST 1). Six equal
// cards from catalog.js (copy and counts computed there), at most one
// `Recommended`, the Connected now strip when an agent the user may use is
// online, and search over the whole catalog (results replace the cards).
import React, { useId, useMemo } from "react";
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
  catalogEntry,
  runSourceCard,
  searchCatalog,
  searchStepCards,
  stepCardsFor,
  stepWayCount,
  stepWayNames,
  stepCardShown,
} from "./catalog";
import SourceLogo from "./SourceLogo";
import CatalogTile from "./CatalogTile";

// L7c g2 (picker:search-no-match): a search with no match names every way
// step 1 offers (Prompt, Agent graph, the endpoint, A2A and MCP entries).
const listNames = (names) =>
  names.length > 1
    ? `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`
    : names[0] ?? "";

function SourceCard({ card, keyNum, recommended, onPick, why }) {
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
      // code sentence on all six).
      title={keyNum ? `${card.tip}  ·  Press ${keyNum}` : card.tip}
      size="small"
      placement="top"
      arrow
      enterDelay={600}
    >
      <Box
        role="button"
        tabIndex={0}
        aria-keyshortcuts={String(keyNum)}
        data-testid={`source-card-${card.id}`}
        onClick={(e) => onPick(card.id, e.currentTarget)}
        onKeyDown={(e) => {
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
          cursor: "pointer",
          bgcolor: "background.paper",
          outline: "none",
          minWidth: 0,
          "& .sc-key": { opacity: 0, transition: "opacity 120ms" },
          "&:hover, &:focus-visible": {
            borderColor: "purple.500",
            bgcolor: "purple.o5",
          },
          "&:hover .sc-key, &:focus-visible .sc-key": { opacity: 1 },
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
          typography="s2"
          color="text.secondary"
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            minHeight: 36,
          }}
        >
          {why ? `Matches ${why}` : card.line}
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
          {recommended ? (
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
        {keyNum ? (
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
}) {
  // IT-R2 G1 P5: `Recommended` only from context (the dataset's baseline
  // run kind, or the run being re-tested); none otherwise.
  // IT-R4 G1 P4: cards in the order for the dataset kind; `Recommended`
  // on the first card only (the kind's reason as its tooltip).
  // options-truth L1 B: the door's `recommended` ({card, reason}) wins over
  // the dataset kind's first card; L7c g2: that card is shown first.
  const agentNoticeId = useId();
  const { cards, rec: kindRec } = stepCardsFor(_datasetKind, recommended);
  const rec = kindRec?.card ?? null;
  const n = stepWayCount();
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
    () => (searching ? searchStepCards(query, names) : []),
    [searching, query, names],
  );
  const results = useMemo(
    () => (searching ? searchCatalog(query) : null),
    [searching, query],
  );
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
          title={STEP1_COPY.disambiguation}
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
          if (e.key === "Enter" && cardHits.length) {
            e.preventDefault();
            onPickCard(cardHits[0].card, e.currentTarget);
          } else if (e.key === "Enter" && results?.length) {
            e.preventDefault();
            onPickEntry(results[0].id);
          }
          if (e.key === "Escape" && query) {
            e.stopPropagation();
            onQuery("");
          }
        }}
        placeholder={STEP1_COPY.searchPlaceholder(n)}
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
                  card={runSourceCard(h.card)}
                  why={h.why}
                  recommended={null}
                  onPick={onPickCard}
                />
              </Box>
            ))}
            {results.slice(0, 12).map((e, i) => (
              <CatalogTile
                key={e.id}
                entry={e}
                first={i === 0 && !cardHits.length}
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
              {`No match for "${query.trim()}". Try ${listNames(stepWayNames())}.`}
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
};
