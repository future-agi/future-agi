// Explore › Charts (W5-5 B4, AG-06): a board of chart cards. `+ Add chart`
// picks Measure × Chart × Group; each card has a drag handle and ⋯
// Duplicate · Full width · Remove; two columns at ≥ 1280 px. The layout is
// the URL key `board` (boardModel.js), so a reload and Save view keep it.
// Each card renders through BoardCardChart (workbenchPivot.buildPivot).
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  ListItemText,
  Menu,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";
import { groupLabel } from "./workbenchModel";
import {
  ALL_METRICS,
  BOARD_CHARTS,
  BOARD_MAX,
  addCard,
  chartOf,
  canAdd,
  cardTitle,
  duplicateCard,
  moveCard,
  parseBoard,
  removeCard,
  serializeBoard,
  toggleFull,
} from "./boardModel";
import BoardCardChart from "./BoardCardChart";
import RunMetricChart from "./charts/RunMetricChart";
import { hasW5 } from "./w5Api";
import { useDerivedMetrics } from "src/sections/common/DerivedMetrics/useDerivedMetrics";

// W5 B2 on the board: `rm:<metric id>` is a run metric (one number per run).
const RUN_METRIC = "rm:";
const isRunMetric = (key) => String(key ?? "").startsWith(RUN_METRIC);

function BoardCard({ card, index, title, ctx, onAction }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: card.id });
  const [anchor, setAnchor] = useState(null);
  return (
    <Box
      ref={setNodeRef}
      data-testid="board-card"
      data-title={title}
      data-full={card.full ? "1" : "0"}
      sx={{
        gridColumn: card.full ? "1 / -1" : "auto",
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.6 : 1,
        position: "relative",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 0.5 }}
      >
        <IconButton
          size="small"
          aria-label={`Drag ${title}`}
          data-testid="board-drag"
          {...attributes}
          {...listeners}
          sx={{ cursor: "grab" }}
        >
          <Iconify icon="mdi:drag" width={18} />
        </IconButton>
        <IconButton
          size="small"
          aria-label={`Actions for ${title}`}
          onClick={(e) => setAnchor(e.currentTarget)}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
        <Menu
          anchorEl={anchor}
          open={Boolean(anchor)}
          onClose={() => setAnchor(null)}
        >
          {[
            ["duplicate", "Duplicate", !canAdd(ctx.cards)],
            ["full", card.full ? "Half width" : "Full width", false],
            ["remove", "Remove", false],
          ].map(([key, label, disabled]) => (
            <MenuItem
              key={key}
              disabled={disabled}
              onClick={() => {
                setAnchor(null);
                onAction(key, index);
              }}
            >
              <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                {label}
              </ListItemText>
            </MenuItem>
          ))}
        </Menu>
      </Stack>
      {isRunMetric(card.measure) ? (
        ctx.runMetric(card.measure) ? (
          <RunMetricChart
            metric={ctx.runMetric(card.measure)}
            values={ctx.runMetricValues}
            columns={ctx.columns}
            colColors={ctx.colColors}
            title={title}
          />
        ) : (
          <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
            This run metric was removed from the dataset.
          </Typography>
        )
      ) : (
        <BoardCardChart card={card} title={title} ctx={ctx} />
      )}
    </Box>
  );
}

BoardCard.propTypes = {
  card: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  title: PropTypes.string,
  ctx: PropTypes.object.isRequired,
  onAction: PropTypes.func.isRequired,
};

function AddChart({ measures, groups, onAdd, disabled }) {
  const [anchor, setAnchor] = useState(null);
  const [draft, setDraft] = useState({
    measure: "",
    chart: "bars",
    rows: "none",
  });
  const kind = chartOf(draft.chart);
  const pick = (label, key, options) => (
    <TextField
      select
      size="small"
      label={label}
      value={draft[key]}
      onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
      SelectProps={{ MenuProps: { disablePortal: false } }}
      inputProps={{ "aria-label": label }}
      sx={{ minWidth: 220 }}
    >
      {options.map((o) => (
        <MenuItem key={o.key} value={o.key} disabled={Boolean(o.disabled)}>
          {o.label}
        </MenuItem>
      ))}
    </TextField>
  );
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="mdi:plus" width={16} />}
        disabled={disabled}
        onClick={(e) => {
          setDraft((d) => ({ ...d, measure: d.measure || measures[0]?.key }));
          setAnchor(e.currentTarget);
        }}
      >
        Add chart
      </Button>
      <Popover
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <Stack gap={1.5} sx={{ p: 2 }} data-testid="board-add">
          {kind?.measure === false
            ? null
            : pick("Measure", "measure", measures)}
          {isRunMetric(draft.measure) ? (
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ maxWidth: 260 }}
            >
              A run metric is one number per run: the card shows bars by run.
            </Typography>
          ) : (
            <>
              {pick("Chart", "chart", BOARD_CHARTS)}
              {kind?.group === false ? (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ maxWidth: 260 }}
                >
                  {kind.note}
                </Typography>
              ) : (
                pick(
                  "Group",
                  "rows",
                  groups.filter(
                    (g) =>
                      !["path", "row", "expr"].includes(g.key) &&
                      !g.key.startsWith("cc:"),
                  ),
                )
              )}
            </>
          )}
          <Button
            variant="contained"
            size="small"
            disabled={!draft.measure}
            onClick={() => {
              onAdd(
                isRunMetric(draft.measure)
                  ? { measure: draft.measure, chart: "bars", rows: "none" }
                  : kind?.group === false
                    ? {
                        ...draft,
                        measure:
                          kind.measure === false ? ALL_METRICS : draft.measure,
                        rows: "none",
                      }
                    : draft,
              );
              setAnchor(null);
            }}
          >
            Add to board
          </Button>
        </Stack>
      </Popover>
    </>
  );
}

AddChart.propTypes = {
  measures: PropTypes.array.isRequired,
  groups: PropTypes.array.isRequired,
  onAdd: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

const ChartBoard = ({ value, onChange, savedViews, ...ctxIn }) => {
  const cards = useMemo(() => parseBoard(value), [value]);
  const wide = useMediaQuery("(min-width:1280px)");
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  // W5 B2: the dataset's run metrics are board measures (absent until the
  // derived-metrics endpoint is registered).
  const rmQ = useDerivedMetrics(
    hasW5("derivedMetrics") ? ctxIn.datasetId : null,
    (ctxIn.runIds ?? []).filter(Boolean),
  );
  const runMetrics = useMemo(() => rmQ.data?.metrics ?? [], [rmQ.data]);
  const metricOf = (key) =>
    isRunMetric(key)
      ? runMetrics.find((m) => m.id === key.slice(RUN_METRIC.length)) ?? null
      : null;
  const ctx = {
    ...ctxIn,
    cards,
    runMetric: metricOf,
    runMetricValues: rmQ.data?.values ?? {},
  };
  const set = (next) => onChange(serializeBoard(next) || null);
  const titleOf = (card) =>
    cardTitle(card, {
      measureLabel:
        metricOf(card.measure)?.name ??
        ctxIn.measures.find((m) => m.key === card.measure)?.label ??
        card.measure,
      groupLabel: groupLabel(card.rows),
    });
  const onAction = (key, i) => {
    if (key === "duplicate") set(duplicateCard(cards, i));
    else if (key === "full") set(toggleFull(cards, i));
    else set(removeCard(cards, i));
  };
  const visibleMeasures = [
    ...ctxIn.measures.filter((m) => !m.disabled),
    ...runMetrics.map((m) => ({
      key: `${RUN_METRIC}${m.id}`,
      label: `Run metrics ▸ ${m.name}`,
    })),
  ];
  return (
    <Stack gap={1.5} data-testid="chart-board">
      <Stack direction="row" alignItems="center" gap={1.5}>
        <AddChart
          measures={visibleMeasures}
          groups={ctxIn.groups}
          disabled={!canAdd(cards)}
          onAdd={(draft) => set(addCard(cards, draft))}
        />
        {savedViews}
        <Typography typography="s2" color="text.secondary">
          {cards.length
            ? `${cards.length} of ${BOARD_MAX} charts · drag to reorder · Save view keeps the board`
            : "Add charts to build a board; the link and Save view keep it."}
        </Typography>
      </Stack>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={({ active, over }) => {
          if (!over || active.id === over.id) return;
          const from = cards.findIndex((c) => c.id === active.id);
          const to = cards.findIndex((c) => c.id === over.id);
          set(moveCard(cards, from, to));
        }}
      >
        <SortableContext
          items={cards.map((c) => c.id)}
          strategy={rectSortingStrategy}
        >
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: wide ? "repeat(2, minmax(0, 1fr))" : "1fr",
              gap: 1.5,
            }}
          >
            {cards.map((card, i) => (
              <BoardCard
                key={card.id}
                card={card}
                index={i}
                title={titleOf(card)}
                ctx={ctx}
                onAction={onAction}
              />
            ))}
          </Box>
        </SortableContext>
      </DndContext>
    </Stack>
  );
};

ChartBoard.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  savedViews: PropTypes.node,
  datasetId: PropTypes.string,
  runIds: PropTypes.array,
  baselineId: PropTypes.string,
  filtersRaw: PropTypes.string,
  keepIds: PropTypes.object,
  columns: PropTypes.array.isRequired,
  colColors: PropTypes.object,
  measures: PropTypes.array.isRequired,
  groups: PropTypes.array.isRequired,
  k: PropTypes.number,
  isCheck: PropTypes.func,
  onOpenRows: PropTypes.func.isRequired,
  // Trend and Delta bars cards (the pivot's own inputs)
  colsAxis: PropTypes.string,
  runsById: PropTypes.object,
  onHop: PropTypes.func,
  analysis: PropTypes.object,
  candidateId: PropTypes.string,
  candidates: PropTypes.array,
  labelOf: PropTypes.func,
};

export default ChartBoard;
