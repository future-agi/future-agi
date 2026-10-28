import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Dialog,
  Drawer,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useNavigate } from "react-router";
import Iconify from "src/components/iconify";
import { UNITS } from "src/sections/projects/observePopulation";
import { EvidenceQuote } from "./Citation";
import { useCitedImage, useRunUnit } from "./insightsApi";
import { categoriesFor } from "./insightsDistribution";
import {
  evidenceRanges,
  splitByRanges,
  unplacedEvidence,
} from "./insightsEvidence";
import { highlightTitle } from "./insightsFindings";
import {
  formatCost,
  formatCount,
  formatLatency,
  formatSpan,
  formatWhen,
} from "./insightsFormat";
import { unitLink } from "./insightsLinks";
import { useReport } from "./reportContext";
import { AttributePills, ErrorState, FacetChips, Swatch } from "./parts";

export const EXAMPLE_PANEL_WIDTH = 600;

function metricParts(metrics = {}, unit) {
  return [
    unit === "users" &&
      Number.isFinite(metrics.sessions) &&
      `${formatCount(metrics.sessions)} sessions`,
    unit === "users" &&
      Number.isFinite(metrics.active_days) &&
      `${formatCount(metrics.active_days)} active days`,
    unit !== "traces" &&
      Number.isFinite(metrics.traces) &&
      `${formatCount(metrics.traces)} traces`,
    Number.isFinite(metrics.turns) &&
      `${formatCount(metrics.turns)} ${metrics.turns === 1 ? "turn" : "turns"}`,
    Number.isFinite(metrics.latency_ms) &&
      `${formatLatency(metrics.latency_ms)} median latency`,
    Number.isFinite(metrics.duration_s) &&
      `${formatSpan(metrics.duration_s)} long`,
    Number.isFinite(metrics.errors) &&
      `${formatCount(metrics.errors)} ${metrics.errors === 1 ? "error" : "errors"}`,
    metrics.cost > 0 && formatCost(metrics.cost),
  ].filter(Boolean);
}

function Label({ children }) {
  return (
    <Typography
      variant="overline"
      color="text.secondary"
      component="h3"
      sx={{ display: "block", mb: 0.5, lineHeight: "18px" }}
    >
      {children}
    </Typography>
  );
}

Label.propTypes = { children: PropTypes.node };

// "Customer:" / "Assistant:" at the start of a line, optionally after a
// "[Turn 2]" marker: the marker is quiet, the speaker semibold.
const SPEAKER = /(^|\n)(\[Turn (\d+)\] )?([A-Z][\w .'-]{0,24}:)(?= )/g;

function withSpeakers(text, brokeAt) {
  const parts = [];
  let at = 0;
  for (const match of text.matchAll(SPEAKER)) {
    const start = match.index + match[1].length;
    parts.push(text.slice(at, start));
    const broke = match[3] && Number(match[3]) === brokeAt;
    if (match[2])
      parts.push(
        <Box
          component="span"
          key={`t${start}`}
          data-turn={match[3]}
          title={broke ? "The turn where it broke" : undefined}
          sx={
            broke
              ? { color: "error.main", fontSize: 12, fontWeight: 600 }
              : { color: "text.disabled", fontSize: 12 }
          }
        >
          {match[2]}
        </Box>,
      );
    parts.push(
      <Box component="b" key={`s${start}`} sx={{ fontWeight: 600 }}>
        {match[4]}
      </Box>,
    );
    at = start + (match[2] || "").length + match[4].length;
  }
  parts.push(text.slice(at));
  return parts;
}

// A long conversation opens with the end of the turn before the one where it
// broke still in view, so it reads as the middle of something.
const TURN_LEAD_PX = 48;

/**
 * The unit's full content with every evidence quote marked. A long
 * conversation scrolls in its own box, opened at the turn where it broke
 * (else the first mark), so the panel keeps its summary in view.
 */
function HighlightedContent({ content, evidence, brokeAt }) {
  const ranges = useMemo(
    () => evidenceRanges(content, evidence),
    [content, evidence],
  );
  const segments = useMemo(
    () => splitByRanges(content, ranges),
    [content, ranges],
  );
  const box = useRef(null);
  useEffect(() => {
    const container = box.current;
    if (!container) return;
    const turn =
      Number.isFinite(brokeAt) &&
      container.querySelector(`[data-turn="${brokeAt}"]`);
    const target = turn || container.querySelector("mark");
    if (!target) return;
    container.scrollTop = turn
      ? Math.max(0, target.offsetTop - TURN_LEAD_PX)
      : Math.max(0, target.offsetTop - container.clientHeight / 3);
  }, [segments, brokeAt]);
  return (
    <Box
      ref={box}
      data-testid="example-conversation"
      sx={{
        position: "relative",
        maxHeight: "min(60vh, 520px)",
        overflowY: "auto",
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.neutral",
        fontSize: 13,
        lineHeight: 1.7,
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
        "& mark": {
          color: "inherit",
          bgcolor: (theme) => alpha(theme.palette.warning.main, 0.3),
          borderRadius: "3px",
          boxShadow: (theme) =>
            `0 0 0 2px ${alpha(theme.palette.warning.main, 0.3)}`,
        },
      }}
    >
      {segments.map((segment, index) =>
        segment.mark ? (
          <mark
            key={index}
            data-evidence={segment.index}
            title={evidence?.[segment.index]?.supports || undefined}
          >
            {segment.text}
          </mark>
        ) : (
          <React.Fragment key={index}>
            {withSpeakers(segment.text, brokeAt)}
          </React.Fragment>
        ),
      )}
    </Box>
  );
}

HighlightedContent.propTypes = {
  content: PropTypes.string.isRequired,
  evidence: PropTypes.array,
  brokeAt: PropTypes.number,
};

const IMAGE_ALT = "Image cited as evidence";

/**
 * An image the unit's evidence cites, with what the analysis saw in it: a
 * thumbnail that opens full size. The bytes come from the report's own
 * endpoint, as the agent viewed them.
 */
function CitedImage({ runId, unitId, item }) {
  const [open, setOpen] = useState(false);
  const image = useCitedImage(runId, unitId, item.media);
  const caption = item.observation || "";
  // What the analysis saw in it describes the image, small or enlarged.
  const alt = caption || IMAGE_ALT;
  return (
    <Box component="figure" sx={{ m: 0 }}>
      {image.url ? (
        <ButtonBase
          onClick={() => setOpen(true)}
          aria-label="Enlarge image"
          sx={{
            display: "block",
            maxWidth: "100%",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            bgcolor: "background.neutral",
            cursor: "zoom-in",
          }}
        >
          <Box
            component="img"
            src={image.url}
            alt={alt}
            sx={{
              display: "block",
              maxWidth: "100%",
              maxHeight: 200,
              objectFit: "contain",
            }}
          />
        </ButtonBase>
      ) : image.isError ? (
        <Box
          sx={{
            px: 1.25,
            py: 1,
            border: "1px dashed",
            borderColor: "divider",
            borderRadius: 1,
            fontSize: 12,
            color: "text.secondary",
          }}
        >
          This image can&apos;t be shown here. It is in the traces of this
          example.
        </Box>
      ) : (
        <Skeleton
          variant="rounded"
          width={240}
          height={140}
          aria-label="Loading image"
        />
      )}
      {(caption || item.supports) && (
        <Typography
          component="figcaption"
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5 }}
        >
          {caption && (
            <>
              What the analysis saw:{" "}
              <Box component="span" sx={{ color: "text.primary" }}>
                {caption}
              </Box>
            </>
          )}
          {caption && item.supports && <br />}
          {item.supports && `Supports: ${item.supports}`}
        </Typography>
      )}
      <Dialog
        open={open && Boolean(image.url)}
        onClose={() => setOpen(false)}
        maxWidth={false}
        PaperProps={{ "aria-label": IMAGE_ALT }}
      >
        <Box sx={{ position: "relative", p: 1.5 }}>
          <IconButton
            aria-label="Close image"
            onClick={() => setOpen(false)}
            size="small"
            sx={{
              position: "absolute",
              top: 8,
              right: 8,
              bgcolor: "background.paper",
              "&:hover": { bgcolor: "background.neutral" },
            }}
          >
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
          <Box
            component="img"
            src={image.url || undefined}
            alt={alt}
            sx={{
              display: "block",
              maxWidth: "min(90vw, 1568px)",
              maxHeight: "80vh",
              objectFit: "contain",
            }}
          />
          {caption && (
            <Typography
              variant="body2"
              sx={{ mt: 1, maxWidth: 640, fontSize: 13 }}
            >
              {caption}
            </Typography>
          )}
        </Box>
      </Dialog>
    </Box>
  );
}

CitedImage.propTypes = {
  runId: PropTypes.string,
  unitId: PropTypes.string,
  item: PropTypes.shape({
    media: PropTypes.string.isRequired,
    observation: PropTypes.string,
    supports: PropTypes.string,
  }).isRequired,
};

/**
 * One analysed unit: what the model concluded, its facets and numbers, and
 * the unit's own content with the evidence for those conclusions marked.
 */
export default function ExamplePanel({ open, unitId, onClose }) {
  const navigate = useNavigate();
  const report = useReport();
  const query = useRunUnit(report.runId, open ? unitId : null, {
    content: true,
  });
  const data = query.data;
  const unit = report.unit;
  const noun = (UNITS[unit] || UNITS.traces).noun;
  const link = unitLink(report.projectId, unit, data);
  // "Top › Sub", or one name when the sub is folded into its top.
  const path = data
    ? categoriesFor(
        [data.category_id, data.sub_category_id],
        report.categoriesById,
      )
    : [];
  const facets = data?.facets || {};
  const evidence = Array.isArray(facets.evidence) ? facets.evidence : [];
  const preview = unitId ? report.previews.get(unitId) : null;
  const title =
    (preview?.title && highlightTitle({ title: preview.title })) ||
    data?.label ||
    (data?.summary ? highlightTitle({ summary: data.summary }) : "") ||
    data?.unit_id;
  const content = typeof data?.content === "string" ? data.content : "";
  // Quotes are marked in the content where they are found and listed when
  // not; an image (v2 media evidence) is always shown on its own.
  const quotes = evidence.filter((item) => item?.quote);
  const images = evidence.filter(
    (item) => item?.kind === "media" && item.media,
  );
  const loose = content ? unplacedEvidence(content, quotes) : quotes;
  const metrics = metricParts(data?.metrics, unit);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: { width: EXAMPLE_PANEL_WIDTH, maxWidth: "100vw" },
        "aria-label": `Example ${noun}`,
      }}
    >
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ flex: 1 }}
          >
            Example {noun}
          </Typography>
          <IconButton aria-label="Close example" onClick={onClose} size="small">
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 2 }}>
          {query.isLoading ? (
            <Stack spacing={1.5}>
              <Skeleton variant="text" width="70%" height={28} />
              <Skeleton variant="text" width="40%" />
              <Skeleton variant="rounded" height={96} />
              <Skeleton variant="rounded" height={240} />
            </Stack>
          ) : query.isError ? (
            <ErrorState
              message="This example could not be loaded."
              onRetry={() => query.refetch()}
            />
          ) : !data ? (
            <Typography variant="body2" color="text.secondary">
              This example is no longer part of the report&apos;s categories.
            </Typography>
          ) : (
            <Stack spacing={2.25}>
              <Box>
                <Typography
                  variant="subtitle1"
                  component="h2"
                  sx={{
                    fontSize: 16,
                    lineHeight: 1.4,
                    wordBreak: "break-word",
                  }}
                >
                  {title}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {[formatWhen(data.time), data.user_id]
                    .filter(Boolean)
                    .join(" · ")}
                </Typography>
                {path.length > 0 && (
                  <Stack
                    direction="row"
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                    sx={{ mt: 1, gap: 0.25 }}
                  >
                    {path.map((category, index) => (
                      <React.Fragment key={category.id}>
                        {index > 0 && (
                          <Iconify
                            icon="mdi:chevron-right"
                            width={14}
                            sx={{ color: "text.disabled" }}
                          />
                        )}
                        <Button
                          size="small"
                          color="inherit"
                          onClick={() => {
                            onClose();
                            report.openCategory(category.id);
                          }}
                          startIcon={
                            <Swatch
                              color={report.colorOf(category.id)}
                              size={8}
                            />
                          }
                          sx={{
                            px: 0.75,
                            minWidth: 0,
                            fontSize: 12,
                            fontWeight: 500,
                          }}
                        >
                          {category.name}
                        </Button>
                      </React.Fragment>
                    ))}
                  </Stack>
                )}
              </Box>

              {data.summary && (
                <Typography
                  variant="body2"
                  sx={{ fontSize: 13, lineHeight: "21px" }}
                >
                  {data.summary}
                </Typography>
              )}

              {data.lens_answer && data.lens_answer !== data.summary && (
                <Box
                  sx={{
                    p: 1.25,
                    borderRadius: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Label>Answer to the report&apos;s question</Label>
                  <Typography variant="body2" sx={{ fontSize: 13 }}>
                    {data.lens_answer}
                  </Typography>
                </Box>
              )}

              {(facets.topic || Object.keys(facets).length > 0) && (
                <Stack spacing={0.75}>
                  <FacetChips facets={facets} />
                  <AttributePills attributes={facets.attributes} />
                </Stack>
              )}

              {metrics.length > 0 && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {metrics.join(" · ")}
                </Typography>
              )}

              {images.length + loose.length > 0 && (
                <Box>
                  <Label>Evidence</Label>
                  <Stack spacing={0.75}>
                    {images.map((item) => (
                      <CitedImage
                        key={item.media}
                        runId={report.runId}
                        unitId={data.unit_id}
                        item={item}
                      />
                    ))}
                    {loose.map((item, index) => (
                      <Box key={index}>
                        <EvidenceQuote quote={item.quote} dense />
                        {item.supports && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ display: "block", mt: 0.25, pl: 1.25 }}
                          >
                            Supports: {item.supports}
                          </Typography>
                        )}
                      </Box>
                    ))}
                  </Stack>
                </Box>
              )}

              {content ? (
                <Box>
                  <Stack
                    direction="row"
                    alignItems="baseline"
                    justifyContent="space-between"
                  >
                    <Label>
                      {unit === "traces" ? "Trace" : "Conversation"}
                    </Label>
                    {quotes.length > loose.length && (
                      <Typography variant="caption" color="text.secondary">
                        Evidence is highlighted
                      </Typography>
                    )}
                  </Stack>
                  <HighlightedContent
                    content={content}
                    evidence={quotes}
                    brokeAt={
                      Number.isFinite(facets.broke_at_turn)
                        ? facets.broke_at_turn
                        : undefined
                    }
                  />
                </Box>
              ) : data.excerpt ? (
                <Box>
                  <Label>{unit === "traces" ? "Input" : "First message"}</Label>
                  <Box
                    sx={{
                      p: 1.5,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      bgcolor: "background.neutral",
                      fontSize: 13,
                      lineHeight: 1.7,
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {data.excerpt}
                  </Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mt: 0.75 }}
                  >
                    The full {noun} opens in Observe.
                  </Typography>
                </Box>
              ) : null}
            </Stack>
          )}
        </Box>
        {link && (
          <Stack
            direction="row"
            justifyContent="flex-end"
            spacing={1}
            sx={{
              pl: 2.5,
              // Clear of the app's floating assistant buttons.
              pr: 10,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            {unit !== "users" && data?.user_id && (
              <Button
                color="inherit"
                variant="outlined"
                onClick={() =>
                  navigate(
                    `/dashboard/users/${encodeURIComponent(data.user_id)}`,
                  )
                }
              >
                Open user
              </Button>
            )}
            <Button
              variant="contained"
              endIcon={<Iconify icon="mdi:arrow-top-right" width={16} />}
              onClick={() => navigate(link.href)}
            >
              {link.label}
            </Button>
          </Stack>
        )}
      </Stack>
    </Drawer>
  );
}

ExamplePanel.propTypes = {
  open: PropTypes.bool.isRequired,
  unitId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};
