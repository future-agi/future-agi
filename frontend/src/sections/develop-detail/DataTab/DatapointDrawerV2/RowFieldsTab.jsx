import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useParams, useSearchParams } from "react-router-dom";
import useOutline from "src/sections/common/ux/oneview/useOutline";
import { rowSummaryParts } from "./rowSummary";
import OutlineToggle from "src/sections/common/ux/oneview/OutlineToggle";
import FieldCard from "src/sections/common/ValueViewer/FieldCard";
import { isConversationValue } from "src/sections/develop-detail/TaskSuite/itemModel";
import ConversationField from "./ConversationField";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import EvalChip from "./EvalChip";
import EvalDetail from "./EvalDetail";
import FieldEditor from "./FieldEditor";
import MetadataSection from "./MetadataSection";
import OutputCard from "./OutputCard";
import RowFieldsStrip from "./RowFieldsStrip";
import RowSection from "./RowSection";
import RowSectionBar from "./RowSectionBar";
import { mediaCellKind } from "src/sections/common/Media/mediaModel";
import { defaultExpectedFor, isEmptyValue } from "./rowModel";
import { useGetDatasetDetail } from "src/api/develop/develop-detail";
import { useChangeSetStore } from "../Versions/changeSetStore";
import { notify } from "src/sections/common/ux/toast";
import FieldCollabMenu from "src/sections/common/Collab/FieldCollabMenu";

/** the interface contract review: an as-of row deleted (later or by the version) and not back. */
const rowNotInLatest = (row) => {
  const change = row?.row_meta?.change;
  return (
    ["deleted", "deleted_later"].includes(change?.kind) && !change?.in_latest
  );
};
import FieldTranslation from "src/sections/common/Collab/FieldTranslation";
import { TRANSLATE_FIELD_PARAM } from "src/sections/common/Collab/fieldCollabModel";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import { isSurfaceShown } from "src/launch/launchSwitch";

// the interface contract: one small table read for `dataset_config` (the declared schema
// the Schema drawer saves; its save invalidates this key's prefix).
const SCHEMA_READ = { page_size: 1, current_page_index: 0 };

const SIDE_BY_SIDE_PX = 900;

/** Width of an element, live. */
function useWidth(ref) {
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((e) => setW(e[0]?.contentRect?.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

const sectionJson = (fields) =>
  Object.fromEntries(fields.map((f) => [f.column.name, f.value ?? null]));

/**
 * RV#27: the empty fields of a section as one muted line —
 * `Empty · conversation · notes` — each name opens its editor.
 */
const EmptyFieldsLine = ({ fields, canEdit, onEdit }) => (
  <Stack
    direction="row"
    alignItems="center"
    gap={0.75}
    data-testid="row-empty-fields"
    sx={{
      px: 1.25,
      minHeight: 32,
      border: "1px dashed",
      borderColor: "divider",
      borderRadius: 1,
      flexWrap: "wrap",
    }}
  >
    <Typography typography="s2" color="text.disabled">
      Empty
    </Typography>
    {fields.map((f) => (
      <Stack key={f.column.id} direction="row" alignItems="center" gap={0.5}>
        <Typography typography="s2" color="text.disabled">
          ·
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {f.column.name}
        </Typography>
        {canEdit && (
          <ButtonBase
            onClick={() => onEdit(f.column.id)}
            aria-label={`Add a value to ${f.column.name}`}
            sx={{
              typography: "s3",
              color: "primary.main",
              borderRadius: 0.5,
              px: 0.5,
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            Add value
          </ButtonBase>
        )}
      </Stack>
    ))}
  </Stack>
);

EmptyFieldsLine.propTypes = {
  fields: PropTypes.array.isRequired,
  canEdit: PropTypes.bool,
  onEdit: PropTypes.func,
};

/**
 * L1 fix round 1 (the capability contract): how the Row tab lays out a row's
 * sections. Every dataset created on dev has columns with no role, so a row
 * there can hold only `No role` fields. Pure.
 * - `empty`: the row has no field in any section (only then does the tab
 *   say the dataset has no columns);
 * - `noRoleOpen`: `No role` starts open when it is the only section with
 *   fields (it starts folded beside Input / Expected / Generated).
 * @param {{input?: any[], expected?: any[], outputs?: any[], evals?: any[],
 *   human?: any[], metadata?: any[], noRole?: any[]}} model
 */
export function rowFieldsLayout(model) {
  const n = (k) => model?.[k]?.length ?? 0;
  const withRole =
    n("input") +
    n("expected") +
    n("outputs") +
    n("evals") +
    n("human") +
    n("metadata");
  return {
    empty: withRole + n("noRole") === 0,
    noRoleOpen: n("noRole") > 0 && withRole === 0,
  };
}

/**
 * The Row tab (PRODUCT-SPEC §3.4): Formatted | Raw, Diff vs expected,
 * Expand/Collapse all, then Input | Expected, Generated columns (outputs
 * with their eval chips), Evals, Metadata and No role.
 */
// One view (calm, nothing removed): a field's Copy / Edit / `−` and a
// section's Copy show on hover or keyboard focus of that card; a folded
// field keeps its `+`; touch screens always show them.
const HIDE = { opacity: 0 };
const QUIET_CARD_ACTIONS = {
  "@media (hover: hover)": {
    "& [data-testid^='field-card-'] :is(button[aria-label^='Copy '], button[aria-label^='Edit '], [aria-label^='Collapse '])":
      { transition: "opacity 120ms ease" },
    "& [data-testid^='field-card-']:not(:hover):not(:focus-within) :is(button[aria-label^='Copy '], button[aria-label^='Edit '], [aria-label^='Collapse '])":
      HIDE,
    "& [data-testid^='row-section-']:not(:hover):not(:focus-within) button[aria-label*=' as JSON']":
      HIDE,
  },
};

const RowFieldsTab = ({
  model,
  row,
  view,
  edit,
  runsCount,
  onOpenRuns,
  onOpenAnnotations,
  onRerun,
  rerunningId,
  onOpenConversation,
}) => {
  const ref = useRef(null);
  const wide = useWidth(ref) >= SIDE_BY_SIDE_PX;
  const [openSection, setOpenSection] = useState({});
  const layout = rowFieldsLayout(model);
  const [openEval, setOpenEval] = useState(null);
  const [compareWith, setCompareWith] = useState({});
  const { mode, diffOn, asOf, collapseTick = 0 } = view;
  const { dataset: datasetId } = useParams();
  // HO D1 / D2: each field's ⋯ menu; the grid's Translate cell… names one.
  const [rowParams] = useSearchParams();
  const translateField = rowParams.get(TRANSLATE_FIELD_PARAM);
  const [fieldTranslation, setFieldTranslation] = useState({});
  // the interface contract: a column named in the declared schema gets the Form view.
  const { data: schemaRead } = useGetDatasetDetail(
    datasetId,
    { staleTime: 30 * 1000 },
    SCHEMA_READ,
  );
  const declared = schemaRead?.dataset_config?.schema?.declared ?? null;
  const formSchemaOf = (column) => {
    const s = declared?.properties?.[column.name];
    return s && typeof s === "object" ? s : undefined;
  };
  const [q, setQ] = useState("");
  // the interface contract (DE-02): Find covers every section (input, expected, generated,
  // evals, human labels, metadata, no role) by field name.
  const match = (f) =>
    !q || f.column.name.toLowerCase().includes(q.trim().toLowerCase());

  // IT-R1 G5 P10: what the row holds, in one line, above the cards.
  const summary = useMemo(() => rowSummaryParts(model), [model]);
  const rowId = row?.row_id;
  useEffect(() => {
    setOpenEval(null);
  }, [rowId]);

  const allIds = useMemo(
    () =>
      [
        ...model.input,
        ...model.expected,
        ...model.outputs,
        ...model.noRole,
      ].map((f) => f.column.id),
    [model],
  );
  // A1-23: Excel outline `+ / −` per field, remembered per dataset (a
  // field you folded stays folded on every row); `X` / the strip's
  // `Expand all` / `Collapse all` act on all of them.
  const outline = useOutline({
    surface: "rowFields",
    scopeId: datasetId,
    ids: allIds,
    defaultOpen: true,
  });
  const folded = (id) => !outline.isOpen(id);
  const toggleField = (id) => outline.toggle(id);
  // `X` toggles every card (RowView bumps the tick).
  const firstTick = useRef(collapseTick);
  useEffect(() => {
    if (firstTick.current === collapseTick) return;
    firstTick.current = collapseTick;
    if (outline.allOpen) outline.collapseAll();
    else outline.expandAll();
  }, [collapseTick]); // eslint-disable-line react-hooks/exhaustive-deps

  // RV#8: turning Diff on scrolls to the first generated output.
  useEffect(() => {
    if (!diffOn || asOf) return;
    const el = ref.current?.querySelector(
      '[data-testid="row-section-outputs"]',
    );
    el?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [diffOn, asOf]);
  const sectionDefaultOpen = (key) =>
    key === "noRole" ? layout.noRoleOpen : true;
  const sectionOpen = (key) => openSection[key] ?? sectionDefaultOpen(key);
  const toggleSection = (key) =>
    setOpenSection((s) => ({
      ...s,
      [key]: !(s[key] ?? sectionDefaultOpen(key)),
    }));

  const latest = row?.row_meta?.change?.latest_values ?? null;
  const userField = (f) => {
    const id = f.column.id;
    const editing = edit.editingId === id;
    const diffLatest =
      diffOn && asOf && latest && Object.hasOwn(latest, id)
        ? latest[id]
        : undefined;
    const formSchema = formSchemaOf(f.column);
    const tKey = `${row?.row_id}:${id}`;
    // HO D1: the field's translation shows right under its card.
    return [
      <FieldCard
        key={id}
        formSchema={formSchema}
        onFormCommit={
          formSchema && edit.canEdit && datasetId && row?.row_id
            ? (value) => {
                // L1: the real backend writes the field at once.
                if (gridWritesClassic()) {
                  edit.apply(id, value);
                  return;
                }
                useChangeSetStore.getState().addOp(datasetId, {
                  op: "update_cell",
                  row_id: row.row_id,
                  column_id: id,
                  value,
                  before: f.edited ? f.original : f.value,
                });
                notify(
                  `${f.column.name} changed · unsaved until you save a version`,
                  { key: "row-form-edit" },
                );
              }
            : undefined
        }
        name={f.column.name}
        role={f.column.role}
        dataType={f.column.data_type}
        originType={f.column.origin_type}
        value={f.value}
        mode={mode}
        editable={edit.canEdit}
        edited={f.edited}
        previous={f.edited ? f.original : undefined}
        clampLines={8}
        onEdit={() => edit.start(id)}
        onRevert={() => edit.revert(id)}
        expanded={!folded(id) || editing}
        actions={
          <>
            {datasetId && row?.row_id ? (
              <FieldCollabMenu
                datasetId={datasetId}
                rowId={row.row_id}
                columnId={id}
                name={f.column.name}
                autoOpen={translateField === id}
                // the interface contract: as of an older version the row takes no comments;
                // a row not in the latest version has no translation (the
                // translate read is of the latest rows), so no Translate.
                // L1 verify 2 must-fix: comments and translation are the
                // hidden dataset.collab.comments / .translate surfaces in a
                // launch build, so the field's ⋯ does not render there.
                canComment={!asOf && isSurfaceShown("dataset.collab.comments")}
                canTranslate={
                  isSurfaceShown("dataset.collab.translate") &&
                  !(asOf && rowNotInLatest(row))
                }
                onTranslated={(res) =>
                  setFieldTranslation((m) => ({ ...m, [tKey]: res }))
                }
              />
            ) : null}
            <OutlineToggle
              open={!folded(id) || editing}
              onToggle={() => toggleField(id)}
              label={f.column.name}
            />
          </>
        }
      >
        {editing ? (
          <FieldEditor
            dataType={f.column.data_type}
            value={f.value}
            applyRef={edit.applyRef}
            onCancel={edit.cancel}
            onApply={(v) => edit.apply(id, v)}
          />
        ) : diffLatest !== undefined ? (
          <TextDiff from={f.value} to={diffLatest} />
        ) : mode !== "raw" && isConversationValue(f.value) ? (
          // IT-R3 G5 P2: a conversation reads as the chat (JSON on Raw).
          <ConversationField
            value={f.value}
            production={row?.row_meta?.production ?? null}
            onOpenTab={onOpenConversation}
          />
        ) : undefined}
      </FieldCard>,
      fieldTranslation[tKey] ? (
        <FieldTranslation
          key={`${id}:translation`}
          datasetId={datasetId}
          rowId={row.row_id}
          result={fieldTranslation[tKey]}
          onClose={() =>
            setFieldTranslation(({ [tKey]: _gone, ...rest }) => rest)
          }
        />
      ) : null,
    ];
  };

  // RV#27: empty fields fold into one line per section (unless editing).
  const split = (fields) => {
    const shown = fields.filter(match);
    const empty = shown.filter(
      (f) =>
        isEmptyValue(f.value) && !f.edited && edit.editingId !== f.column.id,
    );
    const full = shown.filter((f) => !empty.includes(f));
    return [
      ...full.map(userField),
      ...(empty.length
        ? [
            <EmptyFieldsLine
              key="__empty"
              fields={empty}
              canEdit={edit.canEdit}
              onEdit={edit.start}
            />,
          ]
        : []),
    ];
  };
  // R4 (implementation, dt-5): the sticky section bar (only sections present).
  const mediaFields = [
    ...model.input,
    ...model.expected,
    ...model.outputs,
    ...model.noRole,
  ].filter((f) => mediaCellKind(f.value, f.column?.data_type));
  const sectionBar = [
    model.input.length && { key: "input", label: "Input" },
    model.expected.length && { key: "expected", label: "Expected" },
    model.outputs.length && { key: "outputs", label: "Generated" },
    (model.evals.length || model.human.length) && {
      key: "evals",
      label: "Evals",
    },
    { key: "metadata", label: "Metadata" },
    model.noRole.length && { key: "noRole", label: "No role" },
  ]
    .filter(Boolean)
    .map((x) => ({
      ...x,
      section: x.key,
      selector: `[data-testid="row-section-${x.key === "noRole" ? "no-role" : x.key}"]`,
    }));
  if (mediaFields.length) {
    const first = mediaFields[0];
    const owner = model.input.includes(first)
      ? "input"
      : model.expected.includes(first)
        ? "expected"
        : model.outputs.includes(first)
          ? "outputs"
          : "noRole";
    sectionBar.push({
      key: "media",
      label: "Media",
      count: mediaFields.length,
      section: owner,
      selector: `[data-testid="field-card-${CSS.escape(first.column.name)}"]`,
    });
  }
  const jumpTo = (s) => {
    if (s.section && !sectionOpen(s.section)) toggleSection(s.section);
    // After the section opens (Collapse mounts its body).
    setTimeout(() => {
      const el = ref.current?.querySelector(s.selector);
      el?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      el?.querySelector?.("button, [tabindex]")?.focus?.({
        preventScroll: true,
      });
    }, 140);
  };
  const inputs = split(model.input);
  const expecteds = split(model.expected);
  const outputCount = model.outputs.length;

  return (
    <Box ref={ref} sx={{ minWidth: 0, ...QUIET_CARD_ACTIONS }}>
      <Box
        sx={{
          position: "sticky",
          top: 0,
          zIndex: 3,
          bgcolor: "background.paper",
        }}
        data-row-sticky-head=""
      >
        <RowFieldsStrip
          view={view}
          outline={outline}
          find={
            <TextField
              size="small"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.stopPropagation();
                  setQ("");
                  e.currentTarget.querySelector("input")?.blur();
                }
              }}
              placeholder="Find field…  /"
              inputProps={{
                "aria-label": "Find field",
                "data-fa-search": "row",
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify icon="eva:search-fill" width={16} />
                  </InputAdornment>
                ),
              }}
              sx={{
                width: 150,
                flexShrink: 0,
                "& .MuiInputBase-input": { typography: "s2", py: 0.5 },
              }}
            />
          }
          canDiff={model.expected.length > 0 && outputCount > 0}
          hasExpected={model.expected.length > 0}
          diffCount={outputCount}
          sections={<RowSectionBar sections={sectionBar} onJump={jumpTo} />}
        />
        {summary.length ? (
          // IT-R2 G5 P6: labelled chips, not a cramped unlabeled line.
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            title={summary.join(" · ")}
            data-testid="row-summary"
            sx={{
              px: 2,
              py: 0.625,
              minWidth: 0,
              overflow: "hidden",
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ flexShrink: 0, mr: 0.25 }}
            >
              Row has:
            </Typography>
            {summary.map((part) => (
              <Box
                key={part}
                component="span"
                sx={{
                  px: 0.75,
                  borderRadius: 0.5,
                  typography: "s3",
                  lineHeight: "20px",
                  bgcolor: "action.hover",
                  color: "text.primary",
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                {part}
              </Box>
            ))}
          </Stack>
        ) : null}
      </Box>
      <Stack gap={2.5} sx={{ px: 2, pt: 1.5, pb: 3 }}>
        {(inputs.length > 0 || expecteds.length > 0) &&
          (wide && inputs.length && expecteds.length ? (
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                gap: 1.5,
                alignItems: "start",
              }}
            >
              <RowSection
                title="Input"
                count={inputs.length}
                open={sectionOpen("input")}
                onToggle={() => toggleSection("input")}
                copyJson={() => sectionJson(model.input)}
                copyCount={model.input.length}
                testId="row-section-input"
              >
                {inputs}
              </RowSection>
              <RowSection
                title="Expected"
                count={expecteds.length}
                open={sectionOpen("expected")}
                onToggle={() => toggleSection("expected")}
                copyJson={() => sectionJson(model.expected)}
                copyCount={model.expected.length}
                testId="row-section-expected"
              >
                {expecteds}
              </RowSection>
            </Box>
          ) : (
            <>
              {inputs.length > 0 && (
                <RowSection
                  title="Input"
                  count={inputs.length}
                  open={sectionOpen("input")}
                  onToggle={() => toggleSection("input")}
                  copyJson={() => sectionJson(model.input)}
                  copyCount={model.input.length}
                  testId="row-section-input"
                >
                  {inputs}
                </RowSection>
              )}
              {expecteds.length > 0 && (
                <RowSection
                  title="Expected"
                  count={expecteds.length}
                  open={sectionOpen("expected")}
                  onToggle={() => toggleSection("expected")}
                  copyJson={() => sectionJson(model.expected)}
                  copyCount={model.expected.length}
                  testId="row-section-expected"
                >
                  {expecteds}
                </RowSection>
              )}
            </>
          ))}

        {model.outputs.some(match) && (
          <RowSection
            title="Generated columns on this dataset"
            count={model.outputs.length}
            open={sectionOpen("outputs")}
            onToggle={() => toggleSection("outputs")}
            testId="row-section-outputs"
            hint={
              runsCount > 0 ? (
                <ButtonBase
                  onClick={onOpenRuns}
                  sx={{
                    typography: "s2",
                    color: "text.secondary",
                    borderRadius: 0.5,
                    "&:hover": { color: "primary.main" },
                  }}
                >
                  Also ran in {runsCount} experiment run
                  {runsCount === 1 ? "" : "s"} → Runs
                </ButtonBase>
              ) : (
                "Not run in any experiment yet"
              )
            }
          >
            {model.outputs.filter(match).map((o) => {
              const fallback = defaultExpectedFor(o, model.expected);
              return (
                <OutputCard
                  key={o.column.id}
                  output={o}
                  row={row}
                  mode={mode}
                  diffOn={diffOn && !asOf}
                  expectedOptions={model.expected}
                  compareWithId={
                    compareWith[o.column.id] ?? fallback?.column.id
                  }
                  onCompareWith={(id) =>
                    setCompareWith((s) => ({ ...s, [o.column.id]: id }))
                  }
                  openEvalId={openEval}
                  onToggleEval={(id) =>
                    setOpenEval((cur) => (cur === id ? null : id))
                  }
                  canEdit={edit.canEdit}
                  onRerun={onRerun}
                  rerunning={rerunningId === o.column.id}
                  expanded={!folded(o.column.id)}
                  onToggleExpanded={() => toggleField(o.column.id)}
                />
              );
            })}
          </RowSection>
        )}

        {(model.evals.some(match) || model.human.some(match)) && (
          <RowSection
            title={model.human.length ? "Evals and human labels" : "Evals"}
            count={model.evals.length + model.human.length}
            open={sectionOpen("evals")}
            onToggle={() => toggleSection("evals")}
            testId="row-section-evals"
          >
            <Stack direction="row" gap={0.75} sx={{ flexWrap: "wrap" }}>
              {model.evals.filter(match).map((ev) => (
                <EvalChip
                  key={ev.column.id}
                  name={ev.column.name}
                  verdict={ev.verdict}
                  selected={openEval === ev.column.id}
                  onClick={() =>
                    setOpenEval((cur) =>
                      cur === ev.column.id ? null : ev.column.id,
                    )
                  }
                />
              ))}
              {model.human.filter(match).map((h) => (
                <EvalChip
                  key={h.column.id}
                  name={h.column.name}
                  verdict={h.verdict}
                  onClick={onOpenAnnotations}
                  tooltip="Human label · open Annotations"
                />
              ))}
            </Stack>
            {model.evals
              .filter((ev) => ev.column.id === openEval)
              .map((ev) => (
                <EvalDetail
                  key={ev.column.id}
                  ev={ev}
                  row={row}
                  canEdit={edit.canEdit}
                />
              ))}
          </RowSection>
        )}

        {/* the interface contract review: while Find has a query, Metadata shows only when a
            metadata field or a row-metadata key matches, with that count. */}
        {(!q ||
          model.metadata.some(match) ||
          Object.keys(model.rowMetadata.value ?? {}).some((k) =>
            k.toLowerCase().includes(q.trim().toLowerCase()),
          )) && (
          <RowSection
            title="Metadata"
            count={
              q ? model.metadata.filter(match).length : model.metadata.length
            }
            open={sectionOpen("metadata")}
            onToggle={() => toggleSection("metadata")}
            copyJson={() => ({
              ...sectionJson(model.metadata),
              row_metadata: model.rowMetadata.value ?? {},
            })}
            testId="row-section-metadata"
          >
            {/* the interface contract: metadata columns with a declared schema show as
              cards, so `View as ▾` offers their Form. */}
            {model.metadata.some((f) => formSchemaOf(f.column)) && (
              <Stack gap={1} sx={{ mb: 1 }} data-testid="row-metadata-schema">
                {model.metadata
                  .filter((f) => formSchemaOf(f.column) && match(f))
                  .map(userField)}
              </Stack>
            )}
            <MetadataSection
              fields={model.metadata.filter(
                (f) => !formSchemaOf(f.column) && match(f),
              )}
              rowMeta={model.rowMetadata}
              mode={mode}
              edit={edit}
              metaKey={edit.metaKey}
              // L1 verify 2 must-fix: dev's classic writes have no row
              // metadata write, so no "Other metadata" pencil there.
              canEditRowMeta={!gridWritesClassic()}
            />
          </RowSection>
        )}

        {model.noRole.length > 0 && (
          <RowSection
            title="No role"
            count={model.noRole.length}
            open={sectionOpen("noRole")}
            onToggle={() => toggleSection("noRole")}
            hint="Set a role so experiments map these columns automatically."
            testId="row-section-no-role"
          >
            {split(model.noRole)}
          </RowSection>
        )}
        {layout.empty && (
          <Typography typography="s1" color="text.secondary">
            This dataset has no columns yet.
          </Typography>
        )}
      </Stack>
    </Box>
  );
};

RowFieldsTab.propTypes = {
  model: PropTypes.object.isRequired,
  row: PropTypes.object,
  view: PropTypes.object.isRequired,
  edit: PropTypes.object.isRequired,
  runsCount: PropTypes.number,
  onOpenRuns: PropTypes.func,
  onOpenAnnotations: PropTypes.func,
  onRerun: PropTypes.func,
  rerunningId: PropTypes.string,
  onOpenConversation: PropTypes.func,
};

export default RowFieldsTab;
