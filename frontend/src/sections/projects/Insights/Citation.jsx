import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Paper,
  Popper,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useRunUnit } from "./insightsApi";
import { parseCitations } from "./insightsCitations";
import { highlightTitle } from "./insightsFindings";
import { useReport } from "./reportContext";
import { Swatch } from "./parts";

const OPEN_DELAY = 220;
const CLOSE_DELAY = 160;

/** A quote set off as evidence: a tinted block with an accent rule. */
export function EvidenceQuote({ quote, dense, sx }) {
  if (!quote) return null;
  return (
    <Box
      component="blockquote"
      sx={{
        m: 0,
        py: dense ? 0.5 : 0.75,
        px: dense ? 1 : 1.25,
        borderLeft: "2px solid",
        borderColor: "warning.main",
        borderRadius: "0 4px 4px 0",
        bgcolor: (theme) => alpha(theme.palette.warning.main, 0.1),
        color: "text.primary",
        fontSize: dense ? 12 : 13,
        lineHeight: 1.55,
        fontStyle: "italic",
        overflowWrap: "anywhere",
        ...sx,
      }}
    >
      “{quote}”
    </Box>
  );
}

EvidenceQuote.propTypes = {
  quote: PropTypes.string,
  dense: PropTypes.bool,
  sx: PropTypes.object,
};

// The hover card's body. Highlights and findings already carry a title,
// summary or quote; anything missing is read from the unit itself.
function CitationCard({ n, unitId }) {
  const report = useReport();
  const known = report?.previews?.get(unitId) || {};
  const needsUnit = !known.summary;
  const unit = useRunUnit(report?.runId, unitId, { enabled: needsUnit });
  const data = unit.data;
  const quote =
    known.quote || data?.facets?.evidence?.find((e) => e?.quote)?.quote || "";
  const summary = known.summary || data?.summary || data?.facets?.summary;
  const title =
    (known.title && highlightTitle({ title: known.title })) ||
    data?.label ||
    (summary ? highlightTitle({ summary }) : "");
  const categoryId =
    known.categoryId || data?.sub_category_id || data?.category_id;
  const category = categoryId && report?.categoriesById?.get(categoryId);
  return (
    <Stack spacing={1}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography
          variant="caption"
          fontWeight={600}
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          #{n}
        </Typography>
        {category && (
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="center"
            sx={{ minWidth: 0 }}
          >
            <Swatch color={report.colorOf(category.id)} size={8} />
            <Typography variant="caption" color="text.secondary" noWrap>
              {category.name}
            </Typography>
          </Stack>
        )}
      </Stack>
      {needsUnit && unit.isLoading ? (
        <Stack spacing={0.5}>
          <Skeleton variant="text" width="80%" />
          <Skeleton variant="text" />
          <Skeleton variant="text" width="60%" />
        </Stack>
      ) : (
        <>
          {title && (
            <Typography variant="subtitle2" sx={{ lineHeight: 1.4 }}>
              {title}
            </Typography>
          )}
          {summary && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{
                fontSize: 13,
                display: "-webkit-box",
                WebkitLineClamp: 4,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {summary}
            </Typography>
          )}
          <EvidenceQuote quote={quote} dense />
          {!title && !summary && unit.isError && (
            <Typography variant="body2" color="text.secondary">
              This example could not be loaded.
            </Typography>
          )}
        </>
      )}
      <Typography variant="caption" color="text.disabled">
        Click to open the example
      </Typography>
    </Stack>
  );
}

CitationCard.propTypes = {
  n: PropTypes.number.isRequired,
  unitId: PropTypes.string,
};

/**
 * "#n": opens the cited example on click and previews it on hover or focus
 * (title, summary and the supporting quote).
 */
export function CitationChip({ n, unitId }) {
  const report = useReport();
  const target = unitId || report?.refIndex?.get(n)?.unitId;
  const [anchor, setAnchor] = useState(null);
  const timers = useRef({});
  const cardId = useId();

  useEffect(() => {
    const own = timers.current;
    return () => {
      clearTimeout(own.open);
      clearTimeout(own.close);
    };
  }, []);

  const scheduleOpen = (element) => {
    clearTimeout(timers.current.close);
    clearTimeout(timers.current.open);
    timers.current.open = setTimeout(() => setAnchor(element), OPEN_DELAY);
  };
  const scheduleClose = () => {
    clearTimeout(timers.current.open);
    clearTimeout(timers.current.close);
    timers.current.close = setTimeout(() => setAnchor(null), CLOSE_DELAY);
  };
  const keep = () => clearTimeout(timers.current.close);

  return (
    <>
      <Box
        component="button"
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          setAnchor(null);
          if (target) report?.openExample?.(target);
        }}
        onMouseEnter={(event) => target && scheduleOpen(event.currentTarget)}
        onMouseLeave={scheduleClose}
        onFocus={(event) => target && setAnchor(event.currentTarget)}
        onBlur={scheduleClose}
        onKeyDown={(event) => {
          if (event.key === "Escape") setAnchor(null);
        }}
        aria-label={`Open example ${n}`}
        aria-describedby={anchor ? cardId : undefined}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          minWidth: 22,
          height: 18,
          px: 0.5,
          mx: 0.25,
          border: "1px solid",
          borderColor: anchor ? "primary.main" : "divider",
          borderRadius: 0.75,
          bgcolor: anchor ? "action.selected" : "background.neutral",
          color: anchor ? "primary.main" : "text.secondary",
          font: "inherit",
          fontSize: 11,
          fontWeight: 600,
          fontStyle: "normal",
          lineHeight: 1,
          verticalAlign: "1px",
          cursor: "pointer",
          fontVariantNumeric: "tabular-nums",
          "&:hover": { color: "primary.main", borderColor: "primary.main" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
        }}
      >
        #{n}
      </Box>
      <Popper
        open={Boolean(anchor && target)}
        anchorEl={anchor}
        placement="bottom-start"
        sx={{ zIndex: (theme) => theme.zIndex.modal + 1 }}
        modifiers={[{ name: "offset", options: { offset: [0, 6] } }]}
      >
        <Paper
          id={cardId}
          role="tooltip"
          elevation={0}
          onMouseEnter={keep}
          onMouseLeave={scheduleClose}
          sx={{
            width: 340,
            p: 1.75,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            boxShadow: (theme) => theme.customShadows?.dropdown,
          }}
        >
          {anchor && target && <CitationCard n={n} unitId={target} />}
        </Paper>
      </Popper>
    </>
  );
}

CitationChip.propTypes = {
  n: PropTypes.number.isRequired,
  unitId: PropTypes.string,
};

/** "+N": citations folded away; hovering lists them as chips. */
export function MoreRefs({ refs }) {
  if (!refs?.length) return null;
  return (
    <Tooltip
      arrow
      placement="top"
      componentsProps={{
        tooltip: {
          sx: {
            p: 1,
            bgcolor: "background.paper",
            color: "text.primary",
            border: "1px solid",
            borderColor: "divider",
            boxShadow: (theme) => theme.customShadows?.dropdown,
          },
        },
        arrow: { sx: { color: "background.paper" } },
      }}
      title={
        <Box
          sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, maxWidth: 240 }}
        >
          {refs.map((n) => (
            <CitationChip key={n} n={n} />
          ))}
        </Box>
      }
    >
      <Box
        component="button"
        type="button"
        aria-label={`${refs.length} more ${refs.length === 1 ? "example" : "examples"}`}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          height: 18,
          px: 0.5,
          mx: 0.25,
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 0.75,
          bgcolor: "transparent",
          color: "text.secondary",
          font: "inherit",
          fontSize: 11,
          fontWeight: 600,
          lineHeight: 1,
          verticalAlign: "1px",
          cursor: "default",
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: 1,
          },
        }}
      >
        +{refs.length}
      </Box>
    </Tooltip>
  );
}

MoreRefs.propTypes = { refs: PropTypes.arrayOf(PropTypes.number) };

/**
 * Prose with "#n" citations rendered as chips. `extraRefs` are cited without
 * being written in the text; they follow it. Refs in `hiddenRefs` are left
 * out, and `moreRefs` (if any) render as one "+N" where the first hidden
 * ref was, or at the end.
 */
export function CitedText({
  text,
  extraRefs,
  hiddenRefs,
  moreRefs,
  variant = "body2",
  component = "span",
  sx,
}) {
  const report = useReport();
  const segments = parseCitations(text, report?.refIndex);
  const hide = (n) => hiddenRefs?.has(n);
  let morePlaced = !moreRefs?.length;
  const more = () => {
    if (morePlaced) return null;
    morePlaced = true;
    return <MoreRefs refs={moreRefs} />;
  };
  const parts = segments.map((segment, i) => {
    if (segment.type === "text")
      return <React.Fragment key={i}>{segment.text}</React.Fragment>;
    const visible = segment.refs.filter((n) => !hide(n));
    const folded = visible.length < segment.refs.length;
    return (
      <Box component="span" key={i} sx={{ whiteSpace: "nowrap" }}>
        {visible.map((n) => (
          <CitationChip key={n} n={n} />
        ))}
        {folded && more()}
      </Box>
    );
  });
  const extra = (extraRefs || []).filter((n) => !hide(n));
  const folded = (extraRefs || []).length > extra.length;
  return (
    <Typography variant={variant} component={component} sx={sx}>
      {parts}
      {(extra.length > 0 || (folded && !morePlaced)) && (
        <Box component="span" sx={{ whiteSpace: "nowrap", ml: 0.25 }}>
          {extra.map((n) => (
            <CitationChip key={n} n={n} />
          ))}
          {more()}
        </Box>
      )}
    </Typography>
  );
}

CitedText.propTypes = {
  text: PropTypes.string,
  extraRefs: PropTypes.arrayOf(PropTypes.number),
  hiddenRefs: PropTypes.instanceOf(Set),
  moreRefs: PropTypes.arrayOf(PropTypes.number),
  variant: PropTypes.string,
  component: PropTypes.elementType,
  sx: PropTypes.object,
};
