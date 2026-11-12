import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import HoverCard from "src/sections/common/ux/oneview/HoverCard";
import CopyButton from "src/sections/common/ux/CopyButton";
import SplitChips from "src/sections/common/Datasets/SplitChips";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import { checksOf, expectedSentence, whyChips } from "../resultsSimple";
import CheckChips from "src/sections/task-compare/CheckChips";
import { rowLabelOf } from "src/sections/common/Runs/runNumbers";
import MediaValue from "src/sections/common/Media/MediaValue";
import { conversationOf } from "src/sections/common/Media/mediaRef";
import GridValueSummary from "src/sections/common/Values/GridValueSummary";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import { failingTurnOf } from "../retestModel";
import { markText, useReviewMarks, worseEvalsOf } from "../reviewMarks";
import {
  CellAction,
  HoverActions,
  ClampedText,
  MutedText,
  cellBoxSx,
  cellPropTypes,
  extraFieldCount,
  extraFieldsPreview,
  linesFor,
  primaryText,
} from "./cellCommon";
import { previewFieldText } from "src/sections/common/Preview/distinguishingField";
import { isRawFormat, rawValueText } from "./rawValue";
import { displayInputs } from "./displayInputs";

/** The input / expected value as a chat chip or media, else null. */
const richCellOf = (value) => {
  if (value == null || value === "") return null;
  const looksStructured =
    typeof value !== "string" ||
    /^\s*[[{]/.test(value) ||
    /^(https?:|data:|\/)/.test(value);
  if (!looksStructured) return null;
  // Calls first (a call has turns too); mediaCellKind leaves chats to the
  // chat chip.
  if (mediaCellKind(value, undefined))
    return <MediaValue value={value} size="cell" />;
  if (conversationOf(value))
    return <GridValueSummary value={value} dataType="json" bare />;
  return null;
};

/** A JSON-object string → its primary text (`{"answer": …}` → the answer). */
const primaryOfJson = (value) => {
  if (typeof value !== "string" || !/^\s*\{/.test(value)) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? { text: primaryText(parsed), extra: extraFieldCount(parsed) }
      : null;
  } catch {
    return null;
  }
};

// Example columns, shown once per row (PRODUCT-SPEC §3.10.7): Row, Splits,
// Input (primary field + `+2 fields`), Expected (baseline run's version,
// `changed in v6` with a hover word diff) and metadata.

/**
 * One cell design, density-driven (one view, A3-04): `compact` rows are one
 * line (text and its markers side by side); every other density clamps the
 * text to the row height with the markers on their own line.
 */
export const isCompactCell = (context) => context?.density === "compact";
export const compactCellSx = {
  ...cellBoxSx,
  py: 0.75,
  flexDirection: "row",
  alignItems: "center",
  gap: 0.75,
};

export const RowCell = ({ data, context }) => {
  const flaky = context?.flakyRows?.has?.(data?.example_id);
  // IT-R5 G3 (P3): the row's vote, by its number (`✓ B`, `= tie`).
  const mark = useReviewMarks((st) => st.marks[data?.example_id]);
  // IT-R5 G3 (P2): which eval(s) made it worse (`Tone ↓`), primary first.
  const worse = context?.baselineId
    ? worseEvalsOf(data, {
        baselineId: context.baselineId,
        defsByKey: context.defsByKey,
        primaryKey: context.primaryMetricKey,
      })
    : [];
  const vote = markText(mark);
  return (
    <Stack
      sx={
        isCompactCell(context)
          ? { ...compactCellSx, gap: 0.5, flexWrap: "wrap", rowGap: 0 }
          : { ...cellBoxSx, gap: 0.25 }
      }
      data-row-cell=""
    >
      <Stack direction="row" alignItems="center" gap={0.5} sx={{ minWidth: 0 }}>
        <Typography
          typography="s2_1"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {context?.taskSuite
            ? rowLabelOf(data?.row_order, "task_suite")
            : data?.row_order}
        </Typography>
        {flaky && (
          <CustomTooltip
            show
            size="small"
            placement="right"
            arrow
            title="Flaky: trials disagree on at least one eval"
          >
            <Box
              sx={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: "amber.500",
                flexShrink: 0,
              }}
            />
          </CustomTooltip>
        )}
        {vote ? (
          <Box
            component="span"
            data-testid="row-review-mark"
            title={`You voted: ${mark.choice === "tie" ? "tie" : mark.blind ? "voted" : `${mark.choice} is better`}`}
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: mark.choice === "tie" ? "text.secondary" : "primary.main",
              whiteSpace: "nowrap",
            }}
          >
            {vote}
          </Box>
        ) : null}
      </Stack>
      {worse.length ? (
        <CustomTooltip
          show
          size="small"
          placement="right"
          arrow
          title={`Worse than the baseline on ${worse.map((w) => w.name).join(", ")}`}
        >
          <Box
            component="span"
            data-testid="row-worse-evals"
            sx={{
              typography: "s3",
              color: "error.dark",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: "100%",
            }}
          >
            {`${worse[0].name} ↓${worse.length > 1 ? ` +${worse.length - 1}` : ""}`}
          </Box>
        </CustomTooltip>
      ) : null}
    </Stack>
  );
};
RowCell.propTypes = cellPropTypes;

export const SplitsCell = ({ data, context }) => {
  const splits = (data?.splits ?? []).map(
    (name) =>
      context?.splitsByName?.[name] ?? { id: name, name, color: "default" },
  );
  if (!splits.length)
    return (
      <Box sx={cellBoxSx}>
        <MutedText>No split</MutedText>
      </Box>
    );
  return (
    <Box sx={cellBoxSx}>
      <SplitChips
        splits={splits}
        max={2}
        size="small"
        onClick={(split) =>
          context?.onFilter?.({ type: "split", values: [split.name] })
        }
      />
    </Box>
  );
};
SplitsCell.propTypes = cellPropTypes;

/** Row hover actions (RC#19): Side by side · Open · Copy input. */
const RowHoverActions = ({ data, context, text }) => (
  <HoverActions>
    {context?.onSideBySide && (
      <CellAction
        title="Open side by side: every run's steps in lanes"
        icon="mdi:compare-horizontal"
        onClick={() => context.onSideBySide(data)}
      />
    )}
    {context?.onOpenRow && (
      <CellAction
        title="Open row (Enter)"
        icon="mdi:arrow-expand"
        onClick={() => context.onOpenRow(data)}
      />
    )}
    {text ? (
      <CellAction
        title="Copy input"
        icon="mdi:content-copy"
        onClick={async () => {
          const ok = await copyText(text);
          notify(ok ? "Input copied" : "Couldn't copy the input", {
            key: "copy-input",
            variant: ok ? "success" : "error",
          });
        }}
      />
    ) : null}
  </HoverActions>
);
RowHoverActions.propTypes = { ...cellPropTypes, text: PropTypes.string };

/**
 * iterate r2 G3 (P1): a multi-turn row from production shows where it
 * failed — `8 turns · failed at turn 5` and the customer message that turn
 * answered (not the first one).
 */
const FailingTurnInput = ({ turn, data, context }) => {
  const compact = isCompactCell(context);
  const chip = (
    <Chip
      size="small"
      label={`${turn.total} turns · failed at turn ${turn.n}`}
      data-testid="input-failing-turn"
      sx={{
        height: 20,
        borderRadius: 0.5,
        typography: "s3",
        fontWeight: 500,
        flexShrink: 0,
        bgcolor: (t) => `${t.palette.error.main}14`,
        color: "red.700",
      }}
    />
  );
  const text = turn.customer ?? "";
  return (
    <Stack
      sx={compact ? compactCellSx : { ...cellBoxSx, gap: 0.5 }}
      direction={compact ? "row" : "column"}
      data-input-failing-turn=""
    >
      <RowHoverActions data={data} context={context} text={text} />
      {chip}
      <Box sx={{ flex: 1, minWidth: 0 }}>
        {text ? (
          <ClampedText
            text={text}
            lines={compact ? 1 : Math.max(1, linesFor(context?.rowHeight, 24))}
            highlight={context?.search}
          />
        ) : (
          <MutedText>No customer message at that turn</MutedText>
        )}
      </Box>
    </Stack>
  );
};
FailingTurnInput.propTypes = {
  turn: PropTypes.object.isRequired,
  ...cellPropTypes,
};

/** `+N fields`: on hover, the fields the cell does not show, `name: value`. */
const ExtraFieldsHover = ({ preview, children }) => (
  <CustomTooltip
    show={preview.fields.length > 0}
    size="small"
    placement="top"
    arrow
    disableInteractive
    title={
      <Box data-testid="input-extra-fields-hover">
        {preview.fields.map(({ name, text }) => (
          <Typography
            key={name}
            component="div"
            typography="s3"
            sx={{ wordBreak: "break-word" }}
          >
            {`${name}: ${text}`}
          </Typography>
        ))}
        {preview.more > 0 && (
          <Typography component="div" typography="s3" sx={{ opacity: 0.8 }}>
            {`+${preview.more} more`}
          </Typography>
        )}
      </Box>
    }
  >
    {children}
  </CustomTooltip>
);
ExtraFieldsHover.propTypes = {
  preview: PropTypes.shape({
    fields: PropTypes.arrayOf(
      PropTypes.shape({ name: PropTypes.string, text: PropTypes.string }),
    ),
    more: PropTypes.number,
  }).isRequired,
  children: PropTypes.element.isRequired,
};

export const InputCell = ({ data, context }) => {
  // A native run's input arrives as `{values: {<column id>: value}}`: read
  // it by column name (the text alone when there is one value).
  const inputs = displayInputs(data?.inputs, context?.inputNames);
  // L7c g6: Raw JSON values shows every input as one JSON object, as
  // stored (a native run's envelope keeps its column ids).
  if (isRawFormat(context))
    return <RawJsonCell data={data} context={context} value={data?.inputs} />;
  const extra = extraFieldCount(inputs);
  // W5 C-H6: the field that differs most across the view (else the first),
  // chosen over the inputs as named here.
  const previewField = context?.inputPreviewField ?? context?.previewField;
  const previewText = previewFieldText(inputs, previewField);
  const text = previewText ?? primaryText(inputs);
  // The other fields for the `+N fields` hover, after the one shown here.
  const others =
    extra > 0
      ? extraFieldsPreview(inputs, previewText != null ? previewField : null)
      : { fields: [], more: 0 };
  const turn = failingTurnOf(data);
  if (turn)
    return <FailingTurnInput turn={turn} data={data} context={context} />;
  const rich = richCellOf(text);
  if (rich)
    return (
      <Stack
        sx={
          isCompactCell(context)
            ? compactCellSx
            : { ...cellBoxSx, justifyContent: "center" }
        }
        data-input-rich=""
      >
        <RowHoverActions data={data} context={context} text={text} />
        <Stack direction="row" alignItems="center" gap={1} sx={{ minWidth: 0 }}>
          <Box
            sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}
          >
            {rich}
          </Box>
          {extra > 0 ? (
            <ExtraFieldsHover preview={others}>
              <Typography
                typography="s3"
                color="text.disabled"
                noWrap
                sx={{ flexShrink: 0 }}
              >
                {`+${extra}`}
              </Typography>
            </ExtraFieldsHover>
          ) : null}
        </Stack>
      </Stack>
    );
  if (isCompactCell(context))
    return (
      <Stack sx={compactCellSx}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {text ? (
            <ClampedText text={text} lines={1} highlight={context?.search} />
          ) : (
            <MutedText>Empty</MutedText>
          )}
        </Box>
        {extra > 0 && (
          // `+4 fields`: each other field and its value on hover.
          <ExtraFieldsHover preview={others}>
            <Typography
              typography="s3"
              color="text.disabled"
              noWrap
              sx={{ flexShrink: 0 }}
              data-testid="input-extra-fields"
            >
              {`+${extra} ${extra === 1 ? "field" : "fields"}`}
            </Typography>
          </ExtraFieldsHover>
        )}
        <RowHoverActions data={data} context={context} text={text} />
      </Stack>
    );
  return (
    <Stack sx={cellBoxSx}>
      <RowHoverActions data={data} context={context} text={text} />
      {text ? (
        <ClampedText
          text={text}
          lines={linesFor(context?.rowHeight, extra ? 24 : 0)}
          highlight={context?.search}
        />
      ) : (
        <MutedText>Empty</MutedText>
      )}
      {extra > 0 && (
        <ExtraFieldsHover preview={others}>
          <Chip
            size="small"
            label={`+${extra} ${extra === 1 ? "field" : "fields"}`}
            sx={{
              alignSelf: "flex-start",
              height: 20,
              borderRadius: 0.5,
              typography: "s3",
              bgcolor: "action.hover",
              color: "text.secondary",
            }}
          />
        </ExtraFieldsHover>
      )}
    </Stack>
  );
};
InputCell.propTypes = cellPropTypes;

/** L7c g6: one cell's stored value as JSON (Raw JSON values on). */
function RawJsonCell({ data, context, value }) {
  const text = rawValueText(value);
  return (
    <Stack
      sx={isCompactCell(context) ? compactCellSx : cellBoxSx}
      data-raw-json=""
    >
      <RowHoverActions data={data} context={context} text={text} />
      {text ? (
        <Box sx={{ fontFamily: "monospace", minWidth: 0, flex: 1 }}>
          <ClampedText
            text={text}
            lines={isCompactCell(context) ? 1 : linesFor(context?.rowHeight, 0)}
            highlight={context?.search}
          />
        </Box>
      ) : (
        <MutedText>Empty</MutedText>
      )}
    </Stack>
  );
}
RawJsonCell.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
  value: PropTypes.any,
};

/** The raw expected value as text (Advanced cell and the hover). */
const rawExpected = (expected) => {
  const v = expected?.value;
  if (v == null) return "";
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
};

/** `changed in v6` with a hover word diff of the expected value. */
function ChangedInChip({ expected, changedIn, from, to }) {
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title={
        <Box sx={{ maxWidth: 420, p: 0.5 }}>
          <Typography typography="s3" sx={{ mb: 0.5, opacity: 0.8 }}>
            {`Expected as of v${expected?.version} → v${changedIn}`}
          </Typography>
          <TextDiff from={from} to={to} />
        </Box>
      }
    >
      <Chip
        size="small"
        label={`changed in v${changedIn}`}
        sx={{
          alignSelf: "flex-start",
          height: 20,
          borderRadius: 0.5,
          typography: "s3",
          fontWeight: 500,
          bgcolor: (t) => `${t.palette.warning.main}1F`,
          color: "warning.dark",
        }}
      />
    </CustomTooltip>
  );
}
ChangedInChip.propTypes = {
  expected: PropTypes.object,
  changedIn: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  from: PropTypes.string,
  to: PropTypes.string,
};

export const ExpectedCell = ({ data, context }) => {
  if (isRawFormat(context))
    return (
      <RawJsonCell
        data={data}
        context={context}
        value={data?.expected?.value}
      />
    );
  const expected = data?.expected;
  const name = expected?.primary;
  const value = name ? expected?.value?.[name] : primaryText(expected?.value);
  // A JSON-object expected reads as its main
  // field (`{"answer": …}` → the answer); the raw JSON stays in the hover.
  const jsonPrimary = primaryOfJson(value);
  const text = jsonPrimary ? jsonPrimary.text : primaryText(value);
  const changedIn = expected?.changed_in?.[0];
  const latest = name ? expected?.latest?.[name] : null;
  // A3-07, one view: the goal text (the checks as one sentence for a task
  // suite) + a chip per check; the raw expected JSON is one hover away with
  // Copy. Compact rows show the sentence on one line (chips in the hover).
  const checks = checksOf(expected);
  const compact = isCompactCell(context);
  const sentence = checks ? expectedSentence(expected) : text;
  const raw = rawExpected(expected);
  const reserved = (checks ? 24 : 0) + (changedIn ? 24 : 0);
  const changed = changedIn ? (
    <ChangedInChip
      expected={expected}
      changedIn={changedIn}
      from={sentence}
      to={
        checks
          ? expected?.latest != null
            ? expectedSentence({ ...expected, value: expected.latest })
            : sentence
          : primaryText(latest)
      }
    />
  ) : null;
  const body = sentence ? (
    <ClampedText
      text={sentence}
      lines={compact ? 1 : linesFor(context?.rowHeight, reserved)}
      highlight={context?.search}
    />
  ) : expected?.reason === "no_reference_column" ? (
    // W4 (walk 1111): the experiment has no Expected answers column; say
    // which one to choose and where.
    <CustomTooltip
      show
      size="small"
      placement="top"
      arrow
      title="This experiment has no Expected answers column. Choose one in Edit › Data, under Expected answers column."
    >
      <Box sx={{ minWidth: 0 }} data-testid="expected-no-column">
        <MutedText>No expected column · choose one in Edit › Data</MutedText>
      </Box>
    </CustomTooltip>
  ) : (
    <MutedText>No expected value</MutedText>
  );
  const withCard = raw ? (
    <HoverCard
      component="div"
      trigger={body}
      // A click still opens the row (the grid's own click); the card is a
      // hover away and holds Copy.
      triggerProps={{
        onClick: undefined,
        onKeyDown: undefined,
        tabIndex: -1,
        role: undefined,
        "aria-expanded": undefined,
      }}
      triggerSx={{ minWidth: 0 }}
      title="Expected (raw)"
      maxWidth={440}
      actions={<CopyButton value={raw} tooltip="Copy expected JSON" />}
    >
      <Stack gap={0.75}>
        {compact && checks ? <CheckChips checks={checks} /> : null}
        <Box
          component="pre"
          sx={{
            m: 0,
            maxHeight: 280,
            overflow: "auto",
            typography: "s3",
            fontFamily: "monospace",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "text.primary",
          }}
        >
          {raw.slice(0, 4000)}
        </Box>
      </Stack>
    </HoverCard>
  ) : (
    body
  );
  if (compact)
    return (
      <Stack sx={compactCellSx}>
        <Box sx={{ flex: 1, minWidth: 0 }}>{withCard}</Box>
        {changed}
      </Stack>
    );
  return (
    <Stack sx={{ ...cellBoxSx, gap: 0.5 }}>
      {changed}
      {withCard}
      {checks ? <CheckChips checks={checks} /> : null}
    </Stack>
  );
};
ExpectedCell.propTypes = cellPropTypes;

/**
 * Why a row is in Regressed / Improved / Changed (RC#1): up to 2 chips,
 * worst first; a chip scrolls to that metric column and flashes it.
 */
export const WhyCell = ({ data, context }) => {
  const chips = whyChips(data, {
    runIds: Object.keys(context?.runsById ?? {}),
    baselineId: context?.baselineId,
    defsByKey: context?.defsByKey,
    runsById: context?.runsById,
    show: context?.show ?? "changed",
  });
  if (!chips.length)
    return (
      <Box sx={cellBoxSx}>
        <MutedText>No eval moved</MutedText>
      </Box>
    );
  return (
    <Stack sx={{ ...cellBoxSx, gap: 0.5 }}>
      {chips.map((c) => (
        <Chip
          key={`${c.runId}:${c.metricKey}`}
          size="small"
          clickable
          label={`${c.tone === "bad" ? "▼" : "▲"} ${c.text}`}
          onClick={(e) => {
            e.stopPropagation();
            context?.onWhyChip?.(c.metricKey, c.runId);
          }}
          aria-label={`${c.tone === "bad" ? "Regressed" : "Improved"}: ${c.text}. Go to the column.`}
          sx={{
            alignSelf: "flex-start",
            maxWidth: "100%",
            height: 22,
            borderRadius: 0.5,
            typography: "s3",
            fontWeight: 500,
            fontVariantNumeric: "tabular-nums",
            bgcolor: (t) =>
              `${c.tone === "bad" ? t.palette.error.main : t.palette.success.main}1F`,
            color: c.tone === "bad" ? "error.dark" : "success.dark",
          }}
        />
      ))}
    </Stack>
  );
};
WhyCell.propTypes = cellPropTypes;

export const MetaCell = ({ data, context, metaKey }) => {
  const value = data?.metadata?.[metaKey];
  if (value == null || value === "")
    return (
      <Box sx={cellBoxSx}>
        <MutedText>—</MutedText>
      </Box>
    );
  return (
    <Box sx={cellBoxSx}>
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Filter: ${metaKey} = ${value}`}
      >
        <Typography
          typography="s2_1"
          noWrap
          onClick={(e) => {
            e.stopPropagation();
            context?.onFilter?.({
              type: "meta",
              key: metaKey,
              op: "eq",
              value,
            });
          }}
          sx={{
            cursor: "pointer",
            "&:hover": { textDecoration: "underline" },
          }}
        >
          {String(value)}
        </Typography>
      </CustomTooltip>
    </Box>
  );
};
MetaCell.propTypes = cellPropTypes;
