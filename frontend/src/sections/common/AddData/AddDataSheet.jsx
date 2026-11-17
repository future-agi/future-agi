// R2 (L5a) · the Add-data sheet (R2-SPEC §7.3).
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  FormControlLabel,
  IconButton,
  MenuItem,
  LinearProgress,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import { useLocation, useNavigate } from "react-router-dom";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import axios, { endpoints } from "src/utils/axios";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import { latestOf, useDatasetVersions } from "src/api/develop/dataset-versions";
import {
  useCreateSplit,
  useDatasetSplits,
} from "src/api/develop/dataset-splits";
import { useImportCommit, useImportPreview } from "src/api/develop/r2";
import { useAnnotationQueuesList } from "src/api/annotation-queues/annotation-queues";
import { datasetKindOf } from "src/sections/common/Datasets/datasetKind";
import { plural } from "src/sections/common/vocab";
import { notify } from "src/sections/common/ux/toast";
import { useChangeSetStore } from "src/sections/develop-detail/DataTab/Versions/changeSetStore";
import { useVersionDialogs } from "src/sections/develop-detail/DataTab/Versions/versionDialogsStore";
import RowUploadFileModal from "src/sections/develop/AddRowDrawer/UploadFileModal";
import NewDatasetUploadFileModal from "src/sections/develop/AddDatasetDrawer/UploadFileModal";
import { useAddDataStore } from "./addDataStore";
import SourcePane from "./SourcePane";
import ShapePicker from "./ShapePicker";
import MappingPane, { IncludeFlags } from "./MappingPane";
import PreviewPane, { TargetVersionLine } from "./PreviewPane";
import SelectedItemPreview from "./SelectedItemPreview";
import ExpectedSourceControl from "./ExpectedSourceControl";
import SplitTargetControl, { newSplitProblem } from "./SplitTargetControl";
import SourceFilterChips from "./SourceFilterChips";
import KeepAsSource from "./KeepAsSource";
import { useCreateImportSource, useImportJob } from "./importSourcesApi";
import {
  conversationOfCells,
  filterSummary,
  IMPORT_WINDOW_OPTIONS,
  pagedText,
  windowChipLabel,
  importBody,
  importDescription,
  includeEstimate,
  cappedText,
  includedTempIds,
  loadMapping,
  mappingRows,
  autoMapLine,
  normaliseTarget,
  saveMapping,
  summaryCounts,
  targetBody,
  targetProblem,
  toChangeSetOps,
  traceItemLabel,
} from "./mappingModel";
import {
  SHAPE_DISABLED_REASONS,
  SHAPE_UNBLOCK_LABELS,
  SOURCES,
  defaultIncludes,
  defaultShape,
  newDatasetKind,
  shapesFor,
} from "./shapes";
import RunOnRowsButton from "src/sections/common/RunSources/RunOnRowsButton"; // R4-F frozen line
import { revealNewRows } from "src/sections/develop-detail/DataTab/DataGrid/newRowsReveal";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import ProductionDataSheet from "./ProductionDataSheet";

const SHEET_WIDTH = 1120;
// What one preview item is, per source (the all-duplicates sentence).
const ITEM_NOUN = { sessions: "session", traces: "trace", queue: "queue item" };
const NEW = "__new__";
const KIND_LABEL = {
  task_suite: "Task suite",
  conversations: "Conversations",
};

const KindChip = ({ kind }) =>
  KIND_LABEL[kind] ? (
    <Chip
      size="small"
      label={KIND_LABEL[kind]}
      sx={{
        height: 20,
        typography: "s3",
        fontWeight: 500,
        bgcolor: "action.hover",
        color: "text.secondary",
      }}
    />
  ) : null;
KindChip.propTypes = { kind: PropTypes.string };

/** Observe projects (`list_project_ids`), for the default project. */
function useProjects() {
  return useQuery({
    queryKey: ["add-data", "projects"],
    queryFn: () => axios.get(endpoints.project.listProjects()),
    select: (res) => res?.data?.result?.projects ?? [],
    staleTime: 60 * 1000,
  });
}

/** A source's live count through an IM1 preview (no rows written). */
function useSourceCount(body, enabled) {
  return useQuery({
    queryKey: ["add-data", "count", body],
    // W3-5: the count comes first (`count_only` builds no items).
    queryFn: () =>
      axios.post(endpoints.develop.r2.imports, {
        ...body,
        op: "preview",
        count_only: true,
      }),
    select: (res) => res?.data?.result?.total ?? null,
    enabled,
    staleTime: 30 * 1000,
    placeholderData: (prev) => prev,
  });
}

const sessionsBody = (projectId, resolution) =>
  importBody({
    sourceKind: "sessions",
    projectId,
    resolution,
    shapeId: "conversation",
  });

/**
 * The sheet body: all state lives here and resets on every open (the
 * parent keys it per open).
 */
const SheetBody = ({ initial, onClose, setDirty }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const src0 = initial.source ?? {};
  const selectAll = Boolean(src0.select_all);
  const kind0 = SOURCES.some((s) => s.id === src0.kind)
    ? src0.kind
    : "sessions";

  const [sourceKind, setSourceKind] = useState(kind0);
  const [projectId, setProjectId] = useState(src0.project_id ?? null);
  const [sessionIds, setSessionIds] = useState(
    kind0 === "sessions" && !selectAll ? src0.session_ids ?? [] : [],
  );
  const [traceIds, setTraceIds] = useState(
    kind0 === "traces" && !selectAll ? src0.trace_ids ?? [] : [],
  );
  const [resolution, setResolution] = useState(selectAll ? "any" : "Fail");
  const [queueId, setQueueId] = useState(src0.queue_id ?? null);
  const [completedOnly, setCompletedOnly] = useState(true);
  const [target, setTarget] = useState(() => normaliseTarget(initial.target));
  const [shapeId, setShapeId] = useState(
    ["task", "conversation"].includes(initial.shape) ? initial.shape : null,
  );
  const [overrides, setOverrides] = useState({});
  const [mappingOpen, setMappingOpen] = useState(false);
  const [activeTarget, setActiveTarget] = useState(null);
  const [remember, setRemember] = useState(true);
  const [includes, setIncludes] = useState(defaultIncludes);
  // L7b G8: Skip traces already in this dataset (off imports them again).
  const [dedupe, setDedupe] = useState(true);
  const [includesOpen, setIncludesOpen] = useState(false);
  const [expected, setExpected] = useState("empty");
  const [edits, setEdits] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [unchecked, setUnchecked] = useState(() => new Set());
  // Duplicates the user ticked to add again (L5 G5 W0-6).
  const [addAgain, setAddAgain] = useState(() => new Set());
  const [filter, setFilter] = useState(null);
  const [selectedItemId, setSelectedItemId] = useState(null);
  const [reviewDrafts, setReviewDrafts] = useState(false);
  const [hideDuplicates, setHideDuplicates] = useState(true);
  const [widenSessions, setWidenSessions] = useState(false);
  // W3-5: the window the user picked (null = the sheet's default).
  const [windowId, setWindowId] = useState(null);
  const effWindow = windowId ?? (widenSessions ? "30d" : "7d");
  const windowLabel =
    typeof effWindow === "object"
      ? `from ${windowChipLabel(effWindow)}`
      : (
          IMPORT_WINDOW_OPTIONS.find((w) => w.id === effWindow)?.label ?? ""
        ).toLowerCase();
  // Failures from last week already in a dataset when the range widened.
  const [weekDupes, setWeekDupes] = useState(0);
  // An existing split id, `{newName}` (+ New split…, created on add) or null.
  const [split, setSplit] = useState(null);
  const splitId = typeof split === "string" ? split : null;
  // L7b G8: no "Copy as API call" here: the IM1 imports route is a
  // prototype path the backend does not serve yet (ABSENT until it does).
  const [fileOpen, setFileOpen] = useState(false);
  const [commitError, setCommitError] = useState(null);
  // W3-5: an import above 1,000 rows runs as a job; the sheet shows it.
  const [jobId, setJobId] = useState(null);
  const jobFinished = useRef(false);
  const jobQ = useImportJob(jobId);
  // W3-5 (D11): keep this import as a saved source on the dataset.
  const [keepSource, setKeepSource] = useState({
    keep: false,
    name: "",
    window: "24h",
    onRefresh: "pending",
  });

  // ------------------------------------------------------------ reads
  const { data: datasets = [] } = useDevelopDatasetList();
  const { data: projects = [] } = useProjects();
  const effProjectId = projectId ?? projects[0]?.id ?? null;
  const projectName =
    projects.find((p) => p.id === effProjectId)?.name ?? "this project";
  const datasetId = target?.dataset_id ?? null;
  const isNew = Boolean(target?.new);
  // `+ New dataset` opens with an empty name: suggest one once the project
  // name is known (only once; the user's typing wins).
  const autoNamed = useRef(false);
  useEffect(() => {
    if (autoNamed.current || !isNew || !projects.length) return;
    autoNamed.current = true;
    if (target.new.name) return;
    setTarget((t) => ({
      new: { ...t.new, name: `${projectName} · sessions` },
    }));
  }, [isNew, projects.length, projectName, target]);
  const targetDs = datasets.find((d) => d.datasetId === datasetId) ?? null;
  const targetKind = isNew ? target.new.kind : datasetKindOf(targetDs);
  const shapes = shapesFor(sourceKind);
  const effShape =
    shapeId && shapes.some((s) => s.id === shapeId)
      ? shapeId
      : defaultShape(sourceKind, targetKind);
  // L7b G8: a queue's items land as rows (a dataset row stays a row; a
  // session item brings its conversation cell), never a conversations kind.
  const newKind = sourceKind === "queue" ? "rows" : newDatasetKind(effShape);
  const { data: columns } = useGetDatasetColumns(datasetId, {
    enabled: Boolean(datasetId),
  });
  const { data: versions = [] } = useDatasetVersions(datasetId, {
    enabled: Boolean(datasetId),
  });
  const { data: splitData } = useDatasetSplits(datasetId);
  const createSplit = useCreateSplit(datasetId);
  const queuesQ = useAnnotationQueuesList(
    {},
    { enabled: sourceKind === "queue" },
  );
  const queues = useMemo(
    () => (queuesQ.data?.results ?? []).filter((q) => !q.deleted),
    [queuesQ.data],
  );
  const queue = queues.find((q) => q.id === queueId) ?? null;
  useEffect(() => {
    if (sourceKind === "queue" && !queueId && queues.length) {
      const done = queues.find((q) => q.completed_count > 0) ?? queues[0];
      setQueueId(done.id);
    }
  }, [sourceKind, queueId, queues]);

  // Remembered mapping for this project → dataset.
  useEffect(() => {
    if (!datasetId || !effProjectId) return;
    const saved = loadMapping(effProjectId, datasetId);
    if (saved) setOverrides(saved);
  }, [datasetId, effProjectId]);

  // ---------------------------------------------------------- request
  const pinnedSessions = sourceKind === "sessions" ? sessionIds.length : 0;
  const pinnedTraces = sourceKind === "traces" ? traceIds.length : 0;
  const tBody = useMemo(
    () =>
      isNew
        ? targetBody({ new: { name: target.new.name, kind: newKind } })
        : targetBody(target),
    [isNew, target, newKind],
  );
  const problem = targetProblem({
    shapeId: effShape,
    sourceKind,
    targetKind,
    isNew,
  });
  const importable =
    ["sessions", "traces", "queue"].includes(sourceKind) &&
    !(sourceKind === "queue" && !queueId) &&
    !problem &&
    Boolean(effProjectId || sourceKind === "queue");
  const body = useMemo(() => {
    const base = importBody({
      sourceKind,
      projectId: effProjectId,
      sessionIds,
      traceIds,
      resolution,
      queueId,
      completedOnly,
      shapeId: effShape,
      overrides,
      includes,
      dedupe,
      expected,
      target: tBody,
      split: splitId,
    });
    return {
      ...base,
      source: {
        ...base.source,
        ...(sourceKind === "sessions" || sourceKind === "traces"
          ? { window: effWindow }
          : {}),
      },
    };
  }, [
    sourceKind,
    effProjectId,
    sessionIds,
    traceIds,
    resolution,
    queueId,
    completedOnly,
    effShape,
    overrides,
    includes,
    dedupe,
    expected,
    tBody,
    splitId,
    widenSessions,
    effWindow,
  ]);
  // Preview is independent of the new dataset's name.
  const previewBody = useMemo(
    () =>
      isNew ? { ...body, target: { new: { name: "", kind: newKind } } } : body,
    [body, isNew, newKind],
  );
  const bodyKey = JSON.stringify(previewBody);

  // ---------------------------------------------------------- preview
  const previewM = useImportPreview();
  const [preview, setPreview] = useState({
    data: null,
    error: null,
    loading: false,
  });
  const identity = JSON.stringify([
    sourceKind,
    effShape,
    datasetId,
    isNew,
    sessionIds,
    traceIds,
    resolution,
    queueId,
    completedOnly,
  ]);
  const lastIdentity = useRef(identity);
  useEffect(() => {
    if (lastIdentity.current === identity) return;
    lastIdentity.current = identity;
    setEdits({});
    setUnchecked(new Set());
    setAddAgain(new Set());
    setFilter(null);
    setSelectedItemId(null);
    setReviewDrafts(false);
    setWidenSessions(false);
    setWeekDupes(0);
    setEditingId(null);
    setPreview((p) => ({ ...p, data: null }));
  }, [identity]);
  useEffect(() => {
    if (!importable) {
      setPreview({ data: null, error: null, loading: false });
      return undefined;
    }
    setPreview((p) => ({ ...p, loading: true }));
    const t = setTimeout(() => {
      previewM.mutate(JSON.parse(bodyKey), {
        onSuccess: (data) => setPreview({ data, error: null, loading: false }),
        onError: (e) =>
          setPreview((p) => ({
            ...p,
            loading: false,
            error: getRequestErrorMessage(e, "Couldn't preview this import."),
          })),
      });
    }, 220);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bodyKey, importable]);

  const items = useMemo(() => preview.data?.items ?? [], [preview.data]);
  const total = preview.data?.total ?? preview.data?.summary?.total ?? null;
  const counts = preview.data
    ? summaryCounts(preview.data.summary, items, unchecked, addAgain)
    : null;
  // `first 200 of 1,240 matching` when the import stops at its cap.
  const capped = cappedText(preview.data) ?? pagedText(preview.data);
  const matching = preview.data?.matching ?? total;
  // L7b G8: every source whose matches can all be imported already.
  const allMatchingDuplicates = Boolean(
    ["sessions", "traces", "queue"].includes(sourceKind) &&
      items.length > 0 &&
      counts?.add === 0 &&
      items.every((item) => item.status === "duplicate"),
  );
  const firstLoad = importable && !preview.data && !preview.error;
  useEffect(() => {
    if (
      sourceKind === "sessions" &&
      resolution === "Fail" &&
      preview.data &&
      items.length > 0 &&
      counts?.add === 0 &&
      !widenSessions
    ) {
      setWeekDupes(items.length);
      // When every match is already imported, keep the user's filters in
      // place and offer recovery actions in the preview. Widening here used
      // to turn a useful explanation into an empty right pane.
      if (!items.every((item) => item.status === "duplicate")) {
        setWidenSessions(true);
      }
    }
  }, [
    counts?.add,
    items.length,
    preview.data,
    resolution,
    sourceKind,
    widenSessions,
  ]);

  // Source counts (live; never flash 0 while re-counting).
  const sessionsCountQ = useSourceCount(
    sessionsBody(effProjectId, resolution),
    Boolean(effProjectId) && !pinnedSessions,
  );
  // Pinned sessions that all have Resolution = Fail describe themselves so.
  const failNamesQ = useQuery({
    queryKey: ["add-data", "fail-sessions", effProjectId],
    queryFn: () =>
      axios.post(endpoints.develop.r2.imports, {
        ...importBody({
          sourceKind: "sessions",
          projectId: effProjectId,
          resolution: "Fail",
          shapeId: "conversation",
          sample: 500,
        }),
        op: "preview",
      }),
    select: (res) =>
      new Set(
        (res?.data?.result?.items ?? []).flatMap((i) => [
          i.label,
          i.source?.session_id,
        ]),
      ),
    enabled: Boolean(effProjectId) && pinnedSessions > 0,
    staleTime: 30 * 1000,
  });
  const pinnedAllFail =
    pinnedSessions > 0 &&
    Boolean(failNamesQ.data) &&
    sessionIds.every((id) => failNamesQ.data.has(id));
  const descScope = {
    resolution: pinnedAllFail ? "Fail" : resolution,
    pinned: Boolean((pinnedSessions && !pinnedAllFail) || pinnedTraces),
  };
  const tracesCountQ = useSourceCount(
    importBody({
      sourceKind: "traces",
      projectId: effProjectId,
      shapeId: "per_trace",
    }),
    Boolean(effProjectId) && !pinnedTraces,
  );
  const sourceCounts = {
    sessions:
      sourceKind === "sessions" && matching != null
        ? matching
        : pinnedSessions || sessionsCountQ.data,
    traces:
      sourceKind === "traces" && matching != null
        ? matching
        : pinnedTraces || tracesCountQ.data,
    queue: sourceKind === "queue" && total != null ? total : undefined,
  };
  const activeCount =
    sourceKind === "sessions"
      ? sourceCounts.sessions
      : sourceKind === "traces"
        ? sourceCounts.traces
        : sourceCounts.queue;
  const summary = filterSummary({
    sourceKind,
    projectName,
    pinnedCount: sourceKind === "sessions" ? pinnedSessions : pinnedTraces,
    resolution,
    count: activeCount,
    queueName: queue?.name,
    completedOnly,
  });

  // Trace items: read the sampled traces for `support-agent · 4 spans …`.
  const traceIdsShown =
    effShape === "trace_item"
      ? items
          .slice(0, filter ? items.length : 6)
          .map((i) => i.source?.trace_id)
          .filter(Boolean)
      : [];
  const traceReads = useQueries({
    queries: traceIdsShown.map((id) => ({
      queryKey: ["add-data", "trace", id],
      queryFn: () => axios.get(endpoints.project.getTrace(id)),
      select: (res) => traceItemLabel(res?.data?.result),
      staleTime: 5 * 60 * 1000,
    })),
  });
  const traceLabels = Object.fromEntries(
    traceIdsShown.map((id, i) => [id, traceReads[i]?.data ?? null]),
  );

  // ---------------------------------------------------------- mapping
  const columnNames = useMemo(() => {
    const fromItems = items[0]?.cells ? Object.keys(items[0].cells) : [];
    if (fromItems.length) return fromItems;
    return (columns ?? []).map((c) => c.name);
  }, [items, columns]);
  const rows = mappingRows({
    columns: columnNames,
    items,
    overrides,
    sourceKind,
    shapeId: effShape,
    expected,
  });
  const line = autoMapLine(rows, { shapeId: effShape });
  const active = activeTarget ?? rows.find((r) => r.unmapped)?.target ?? null;
  const sampleRoot = conversationOfCells(items[0]?.cells) ?? items[0]?.cells;
  const dsName = targetDs?.name ?? "this dataset";
  const selectedItem =
    items.find((item) => item.temp_id === selectedItemId) ?? null;
  // IT-R4 G5 P3: never claim a wider range found more than it did.
  const widenedMessage =
    sourceKind === "sessions" && widenSessions && weekDupes > 0 && counts
      ? `All ${weekDupes} ${weekDupes === 1 ? "failure" : "failures"} from last week ${weekDupes === 1 ? "is" : "are"} already in ${dsName}. ${
          counts.add > 0
            ? "Showing the last 30 days."
            : "Nothing new in the last 30 days either."
        }`
      : null;
  const rememberLabel = `Remember this mapping for ${projectName} → ${
    isNew ? target.new.name || "the new dataset" : dsName
  }`;

  const dirty =
    Object.keys(overrides).length > 0 ||
    Object.keys(edits).length > 0 ||
    unchecked.size > 0 ||
    addAgain.size > 0;

  // ---------------------------------------------------------- commit
  const commitM = useImportCommit();
  const addCount = counts?.add ?? 0;
  // IT-R5 G5 P7: Escape with sessions selected asks before discarding them.
  const selectedSessions =
    importable && sourceKind === "sessions" ? addCount : 0;
  useEffect(
    () => setDirty(dirty, selectedSessions),
    [dirty, selectedSessions, setDirty],
  );
  const newName = isNew ? target.new.name.trim() : "";
  const description = importDescription({
    count: addCount,
    projectName,
    sourceKind,
    shapeId: effShape,
    ...descScope,
    queueName: queue?.name,
    windowLabel,
  });
  // W3-5: a paged preview (above 1,000 items) names what the user left
  // out instead of every item it adds.
  const paged = Boolean(preview.data?.paged);
  const commitBody = useMemo(
    () =>
      paged
        ? {
            ...body,
            exclude_temp_ids: [...unchecked],
            again_temp_ids: [...addAgain],
          }
        : {
            ...body,
            temp_ids: includedTempIds(items, unchecked, addAgain),
          },
    [body, items, unchecked, addAgain, paged],
  );
  const createSource = useCreateImportSource(datasetId);
  const canKeepSource =
    Boolean(datasetId) &&
    !isNew &&
    (sourceKind === "sessions" || sourceKind === "traces") &&
    !pinnedSessions &&
    !pinnedTraces;
  const sourceNoun = sourceKind === "traces" ? "traces" : "sessions";
  const notImportable = {
    file: "Choose a file above; the upload adds the rows.",
  }[sourceKind];
  const splitProblem =
    split && typeof split === "object"
      ? newSplitProblem(split.newName, splitData?.splits)
      : null;
  const blockReason =
    notImportable ??
    (!target
      ? "Pick where the rows go."
      : isNew && !newName
        ? "Name the new dataset."
        : canKeepSource && keepSource.keep && !keepSource.name.trim()
          ? "Name the source."
          : splitProblem ??
            problem ??
            (preview.loading || firstLoad
              ? "Reading the preview…"
              : addCount === 0
                ? "Nothing new to add with these filters"
                : null));

  // The commit's result (straight from IM1, or from a finished job).
  const finishCommit = async (res) => {
    if (isNew) {
      const n = res?.rows_added ?? addCount;
      onClose();
      navigate(`/dashboard/develop/${res?.dataset_id}?tab=data`);
      enqueueSnackbar(`Created "${newName}" with ${plural(n, "row")}`, {
        variant: "success",
      });
      return;
    }
    const nameToId = Object.fromEntries(
      (columns ?? []).map((c) => [c.name, c.id]),
    );
    // IT-R1 G5 P2: `+ New split…` is created now, after the rows were
    // read, so a cancelled or failed import leaves no empty split.
    let targetSplit = splitId;
    let splitName = splitData?.byId?.[splitId]?.name ?? null;
    if (split && typeof split === "object") {
      try {
        const created = await createSplit.mutateAsync({
          name: split.newName.trim(),
          color: "error",
          description: `Imported from ${projectName}`,
        });
        targetSplit = created?.id ?? null;
        splitName = created?.name ?? split.newName.trim();
        queryClient.invalidateQueries({
          queryKey: ["develop", "dataset-splits", datasetId],
        });
      } catch {
        targetSplit = null;
        splitName = null;
      }
    }
    const ops = toChangeSetOps(res?.operations ?? [], {
      edits,
      columnIdByName: nameToId,
      splitIds: targetSplit ? [targetSplit] : [],
      hint: importDescription({
        count: res?.operations?.length ?? 0,
        projectName,
        sourceKind,
        shapeId: effShape,
        ...descScope,
        queueName: queue?.name,
        windowLabel,
      }),
      kind: effShape === "trace_item" ? "trace" : undefined,
      stamp: `${Date.now().toString(36)}-`,
    });
    const cs = useChangeSetStore.getState();
    ops.forEach((op) => cs.addOp(datasetId, op));
    // IT-R2 G5 P4: the grid narrows to the new rows (`New (6)`) and
    // scrolls to + flashes them.
    revealNewRows(
      datasetId,
      ops.map((o) => o.temp_id),
    );
    if (remember) saveMapping(effProjectId, datasetId, overrides);
    else saveMapping(effProjectId, datasetId, null);
    // W3-5 (D11): keep the import as a saved source on the dataset.
    if (canKeepSource && keepSource.keep) {
      try {
        const { temp_ids: _t, target: _target, ...imp } = body;
        const saved = await createSource.mutateAsync({
          name: keepSource.name.trim(),
          import: imp,
          window: keepSource.window,
          on_refresh: keepSource.onRefresh,
          seen: ops
            .map((o) => o.source?.trace_id ?? o.source?.session_id)
            .filter(Boolean),
        });
        notify(`Saved "${saved?.name}" as a source of ${dsName}`, {
          key: "import-source-saved",
          variant: "success",
        });
      } catch (e) {
        notify(getRequestErrorMessage(e, "Couldn't save the source."), {
          key: "import-source-saved",
          variant: "error",
        });
      }
    }
    onClose();
    const here = location.pathname.startsWith(
      `/dashboard/develop/${datasetId}`,
    );
    if (!here) navigate(`/dashboard/develop/${datasetId}?tab=data`);
    enqueueSnackbar(
      <span>
        {`${plural(ops.length, "row")} added to pending changes`}
        {splitName ? (
          <>
            {" · split "}
            <Box component="span" sx={{ whiteSpace: "nowrap" }}>
              {splitName}
            </Box>
          </>
        ) : null}
      </span>,
      {
        variant: "success",
        action: (key) => (
          <>
            <Button
              size="small"
              color="inherit"
              sx={{ fontWeight: 600 }}
              onClick={() => {
                closeSnackbar(key);
                useVersionDialogs.getState().openSave(datasetId);
              }}
            >
              Save version
            </Button>
            {/* R4-F frozen line: Re-test these rows (D24; lane Rb fills it) */}
            <RunOnRowsButton
              variant="toastAction"
              datasetId={datasetId}
              rowIds={ops.map((o) => o.row_id ?? o.temp_id)}
              label="Re-test these rows"
              onDone={() => closeSnackbar(key)}
            />
          </>
        ),
      },
    );
  };

  const onCommit = () => {
    setCommitError(null);
    commitM.mutate(commitBody, {
      onSuccess: (res) => {
        // W3-5: above 1,000 rows IM1 answers `{job_id, total}`; the sheet
        // follows the job and finishes when it is done.
        if (res?.job_id) {
          jobFinished.current = false;
          setJobId(res.job_id);
          return;
        }
        finishCommit(res);
      },
      onError: (e) =>
        setCommitError(getRequestErrorMessage(e, "Couldn't add the rows.")),
    });
  };
  const job = jobQ.data ?? null;
  useEffect(() => {
    if (!job || job.state !== "done" || jobFinished.current) return;
    jobFinished.current = true;
    finishCommit(job.result);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.state]);
  const jobRunning = Boolean(jobId) && job?.state !== "done";
  const jobText = jobId
    ? `Importing ${(job?.total ?? addCount).toLocaleString("en-US")} ${sourceNoun} · ${(job?.done ?? 0).toLocaleString("en-US")} of ${(job?.total ?? addCount).toLocaleString("en-US")}`
    : null;

  // ---------------------------------------------------------- target
  const setTargetValue = (v) => {
    if (v === NEW)
      setTarget({
        new: {
          name: `${projectName} · ${sourceKind === "traces" ? "traces" : "sessions"}`,
          kind: newKind,
        },
      });
    else setTarget(v ? { dataset_id: v } : null);
    setSplit(null);
  };
  const latest = latestOf(versions);
  const versionText = isNew
    ? "Version: v1 of the new dataset"
    : datasetId
      ? `Version: pending changes → Save as v${(latest?.number ?? 0) + 1}`
      : "Version: pick a dataset";
  const title = isNew
    ? `New dataset from ${sourceKind === "queue" ? queue?.name ?? "an annotation queue" : projectName}`
    : datasetId
      ? `Add data to ${dsName}`
      : "Add data";
  const primaryLabel = isNew
    ? `Create dataset with ${plural(addCount, "row")}`
    : `Add ${plural(addCount, "row")} to pending changes`;

  // ---------------------------------------------------------- render
  const targetPicker = (
    <Stack spacing={1}>
      <TextField
        select
        size="small"
        label="Add to"
        id="add-data-target"
        value={isNew ? NEW : datasetId ?? ""}
        onChange={(e) => setTargetValue(e.target.value)}
        SelectProps={{
          displayEmpty: true,
          renderValue: (v) =>
            v === NEW
              ? "New dataset"
              : v
                ? datasets.find((d) => d.datasetId === v)?.name ?? "Dataset"
                : "Pick a dataset",
        }}
        InputLabelProps={{ shrink: true }}
        sx={{ maxWidth: 420, "& .MuiSelect-select": { typography: "s2" } }}
        inputProps={{ "data-testid": "add-data-target" }}
      >
        <MenuItem value={NEW} sx={{ typography: "s2", gap: 1 }}>
          <Iconify icon="mdi:plus" width={16} />
          New dataset
        </MenuItem>
        {datasets.map((d) => (
          <MenuItem
            key={d.datasetId}
            value={d.datasetId}
            sx={{ typography: "s2", gap: 1, justifyContent: "space-between" }}
          >
            <span>{d.name}</span>
            <KindChip kind={datasetKindOf(d)} />
          </MenuItem>
        ))}
      </TextField>
      {isNew && (
        <TextField
          size="small"
          label="Dataset name"
          value={target.new.name}
          onChange={(e) =>
            setTarget({ new: { name: e.target.value, kind: newKind } })
          }
          error={!newName}
          helperText={!newName ? "Name the new dataset" : undefined}
          sx={{ maxWidth: 420, "& input": { typography: "s2" } }}
          inputProps={{ "aria-label": "Dataset name" }}
        />
      )}
    </Stack>
  );

  const sourceControls = (() => {
    // IT-R1 G5 P4: the sessions filter is the chip row in the middle column.
    if (sourceKind === "traces")
      return (
        <>
          {capped && (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="add-data-capped"
            >
              {capped}
            </Typography>
          )}
        </>
      );
    if (sourceKind === "queue")
      return (
        <>
          <TextField
            select
            size="small"
            value={queueId ?? ""}
            onChange={(e) => setQueueId(e.target.value)}
            SelectProps={{ displayEmpty: true }}
            inputProps={{ "aria-label": "Annotation queue" }}
            sx={{ "& .MuiSelect-select": { typography: "s3", py: 0.5 } }}
          >
            {queues.length === 0 && (
              <MenuItem value="" disabled sx={{ typography: "s2" }}>
                {queuesQ.isLoading ? "Loading queues…" : "No queues yet"}
              </MenuItem>
            )}
            {queues.map((q) => (
              <MenuItem key={q.id} value={q.id} sx={{ typography: "s2" }}>
                {q.name}
              </MenuItem>
            ))}
          </TextField>
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={completedOnly}
                onChange={(e) => setCompletedOnly(e.target.checked)}
                sx={{ p: 0.5 }}
              />
            }
            label={
              <Typography typography="s3">Completed items only</Typography>
            }
          />
        </>
      );
    return null;
  })();

  const selectedPreview = reviewDrafts ? null : (
    <SelectedItemPreview item={selectedItem} />
  );

  const middle = (() => {
    if (sourceKind === "file")
      return (
        <Stack spacing={2}>
          {targetPicker}
          <Typography typography="s2" color="text.secondary">
            CSV, JSON or JSONL; columns are matched by name.
          </Typography>
          <CustomTooltip
            show={!target}
            title="Pick where the rows go first."
            size="small"
            arrow
          >
            <span style={{ alignSelf: "flex-start" }}>
              <Button
                variant="outlined"
                size="small"
                disabled={!target}
                startIcon={
                  <Iconify icon="mdi:file-upload-outline" width={16} />
                }
                onClick={() => setFileOpen(true)}
              >
                Choose a file
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      );
    return (
      <Stack spacing={2}>
        <SourceFilterChips
          sourceKind={sourceKind}
          rangeDays={sourceKind === "sessions" && widenSessions ? 30 : 7}
          windowId={effWindow}
          onWindow={setWindowId}
          projects={projects}
          projectId={effProjectId}
          onProject={setProjectId}
          resolution={resolution}
          onResolution={setResolution}
          pinnedCount={
            sourceKind === "sessions" ? pinnedSessions : pinnedTraces
          }
          onClearPinned={() =>
            sourceKind === "sessions" ? setSessionIds([]) : setTraceIds([])
          }
          count={activeCount}
        />
        {sourceKind === "sessions" && (
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={hideDuplicates}
                onChange={(event) => setHideDuplicates(event.target.checked)}
                sx={{ p: 0.5 }}
              />
            }
            label={
              <Typography typography="s3">
                Hide sessions already in a dataset
              </Typography>
            }
          />
        )}
        {targetPicker}
        <ShapePicker
          sourceKind={sourceKind}
          value={effShape}
          onChange={setShapeId}
          disabledReason={(id) =>
            id === "task" && !isNew && datasetId && targetKind !== "task_suite"
              ? {
                  text: SHAPE_DISABLED_REASONS.taskNeedsSuite,
                  actionLabel: SHAPE_UNBLOCK_LABELS.taskNeedsSuite,
                  onAction: () => {
                    setTargetValue(NEW);
                    setShapeId("task");
                  },
                }
              : null
          }
        />
        {problem && (
          <Typography typography="s2" color="warning.dark" role="alert">
            {problem}
          </Typography>
        )}
        {sourceKind === "traces" && (
          <IncludeFlags
            includes={includes}
            onChange={(id, on) => setIncludes((s) => ({ ...s, [id]: on }))}
            open={includesOpen}
            onToggle={() => setIncludesOpen((o) => !o)}
            estimate={includeEstimate(includes, activeCount ?? 0)}
            dedupe={dedupe}
            onDedupeChange={setDedupe}
          />
        )}
        {importable && (
          <MappingPane
            rows={rows}
            line={firstLoad ? "Mapping columns…" : line}
            open={mappingOpen}
            onToggle={() => setMappingOpen((o) => !o)}
            overrides={overrides}
            onOverride={(to, o) =>
              setOverrides((s) => {
                const next = { ...s };
                if (o) next[to] = o;
                else delete next[to];
                return next;
              })
            }
            activeTarget={active}
            onActive={setActiveTarget}
            sourceKind={sourceKind}
            sampleRoot={sampleRoot}
            remember={remember}
            onRemember={setRemember}
            rememberLabel={rememberLabel}
            loading={preview.loading}
            extraChip={
              sourceKind === "queue" ? (
                <CustomTooltip
                  show
                  title="Every row stores where its expected value came from."
                  size="small"
                  arrow
                >
                  <Chip
                    size="small"
                    label="expected_source = human_correction"
                    sx={{
                      height: 20,
                      typography: "s3",
                      fontFamily: "monospace",
                      bgcolor: "action.hover",
                      color: "text.secondary",
                    }}
                  />
                </CustomTooltip>
              ) : null
            }
          />
        )}
        {selectedPreview}
      </Stack>
    );
  })();

  const previewFooter = importable ? (
    <Stack spacing={1.25}>
      <ExpectedSourceControl
        value={expected}
        onChange={setExpected}
        locked={
          sourceKind === "queue"
            ? "Queue items take the reviewer's correction, else stay empty."
            : undefined
        }
      />
      {datasetId && !isNew ? (
        <SplitTargetControl
          splits={splitData?.splits ?? []}
          value={split}
          onChange={setSplit}
        />
      ) : null}
      {canKeepSource ? (
        <KeepAsSource
          value={keepSource}
          onChange={setKeepSource}
          noun={sourceNoun}
        />
      ) : null}
      <TargetVersionLine text={versionText} />
    </Stack>
  ) : null;

  const reviewContent = (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small"
          variant="text"
          startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
          onClick={() => setReviewDrafts(false)}
          sx={{ typography: "s2", px: 0.5 }}
        >
          Back to sessions
        </Button>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", p: 2 }}>
        <SelectedItemPreview item={selectedItem} />
      </Box>
    </Stack>
  );

  const previewContent = importable ? (
    reviewDrafts ? (
      reviewContent
    ) : (
      <PreviewPane
        items={items}
        counts={counts}
        loading={preview.loading}
        firstLoad={firstLoad}
        error={preview.error}
        sourceKind={sourceKind}
        unchecked={unchecked}
        addAgain={addAgain}
        capped={capped}
        paged={paged}
        onToggle={(id) => {
          const dup =
            items.find((i) => i.temp_id === id)?.status === "duplicate";
          (dup ? setAddAgain : setUnchecked)((s) => {
            const n = new Set(s);
            if (n.has(id)) n.delete(id);
            else n.add(id);
            return n;
          });
        }}
        edits={edits}
        editingId={editingId}
        onEditing={setEditingId}
        onEditCell={(id, name, value) =>
          setEdits((s) => ({ ...s, [id]: { ...(s[id] ?? {}), [name]: value } }))
        }
        editDisabledReason={
          isNew
            ? "Edits apply to pending changes; a new dataset saves as previewed."
            : undefined
        }
        displayKind={effShape === "trace_item" ? "trace" : undefined}
        traceLabels={traceLabels}
        filter={filter}
        onFilter={setFilter}
        footer={previewFooter}
        selectedId={selectedItemId}
        onSelect={setSelectedItemId}
        targetName={dsName}
        hideDuplicates={hideDuplicates}
        allMatchingDuplicates={allMatchingDuplicates}
        itemNoun={ITEM_NOUN[sourceKind] ?? "session"}
        onRemoveResolutionFilter={
          // L7b G8: only a source with a Resolution filter offers removing it.
          ["sessions", "traces"].includes(sourceKind) && resolution !== "any"
            ? () => {
                setResolution("any");
                setWidenSessions(false);
              }
            : undefined
        }
        widenedMessage={widenedMessage}
        onShowDuplicates={
          hideDuplicates && counts?.add === 0 && items.length > 0
            ? () => setHideDuplicates(false)
            : undefined
        }
        onReviewDrafts={() => {
          setSelectedItemId(items[0]?.temp_id ?? null);
          setReviewDrafts(true);
        }}
      />
    )
  ) : (
    <Stack
      spacing={1}
      sx={{ py: 4, alignItems: "center", textAlign: "center" }}
    >
      <Iconify
        icon="mdi:eye-off-outline"
        width={28}
        sx={{ color: "text.disabled" }}
      />
      <Typography typography="s2" color="text.secondary" sx={{ maxWidth: 300 }}>
        {sourceKind === "file"
          ? "The upload shows its own preview."
          : problem ?? "Pick a source to preview."}
      </Typography>
    </Stack>
  );

  return (
    <Stack sx={{ height: "100%" }} data-testid="add-data-sheet">
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography variant="m3" fontWeight="fontWeightMedium" noWrap>
          {title}
        </Typography>
        <KindChip kind={isNew ? newKind : targetKind} />
        <Box sx={{ flex: 1 }} />
        <IconButton
          aria-label="Close"
          onClick={() => onClose(true)}
          size="small"
        >
          <Iconify icon="mingcute:close-line" color="text.primary" />
        </IconButton>
      </Stack>
      <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
        <Box
          sx={{
            width: 260,
            flexShrink: 0,
            borderRight: "1px solid",
            borderColor: "divider",
            overflowY: "auto",
            p: 1.5,
          }}
        >
          <SourcePane
            value={sourceKind}
            onChange={(k) => {
              setSourceKind(k);
              setShapeId(null);
              setCommitError(null);
            }}
            counts={sourceCounts}
            summary={
              sourceKind === "sessions" || sourceKind === "traces"
                ? null
                : summary
            }
            loading={preview.loading || sessionsCountQ.isFetching}
          >
            {sourceControls}
          </SourcePane>
        </Box>
        <Box sx={{ flex: 1, minWidth: 0, overflowY: "auto", p: 2 }}>
          {middle}
        </Box>
        <Box
          sx={{
            width: 420,
            flexShrink: 0,
            borderLeft: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflowY: importable ? "hidden" : "auto",
            p: importable ? 0 : 2,
          }}
          data-testid="add-data-preview"
        >
          {previewContent}
        </Box>
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          pl: 2.5,
          // Room for the app's floating launchers (bottom right).
          pr: 11,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {jobId ? (
            <Stack spacing={0.5} data-testid="add-data-job">
              <Typography typography="s2" color="text.primary" noWrap>
                {jobText}
              </Typography>
              <LinearProgress
                variant="determinate"
                value={
                  job?.total ? Math.round((100 * job.done) / job.total) : 0
                }
                sx={{ maxWidth: 360, height: 4, borderRadius: 2 }}
              />
            </Stack>
          ) : commitError ? (
            <Typography typography="s2" color="error.main" role="alert" noWrap>
              {commitError}
            </Typography>
          ) : null}
        </Box>
        <Button variant="text" color="inherit" onClick={() => onClose(true)}>
          Cancel
        </Button>
        <CustomTooltip
          show={Boolean(blockReason)}
          title={blockReason}
          size="small"
          arrow
        >
          <span>
            <LoadingButton
              variant="contained"
              color="primary"
              loading={commitM.isPending || jobRunning}
              disabled={Boolean(blockReason) || jobRunning}
              onClick={onCommit}
              data-testid="add-data-commit"
            >
              {primaryLabel}
            </LoadingButton>
          </span>
        </CustomTooltip>
      </Stack>

      {sourceKind === "file" && datasetId && (
        <RowUploadFileModal
          open={fileOpen}
          onClose={() => setFileOpen(false)}
          refreshGrid={() =>
            queryClient.invalidateQueries({ queryKey: ["develop"] })
          }
          datasetId={datasetId}
          closeDrawer={() => onClose()}
        />
      )}
      {sourceKind === "file" && isNew && (
        <NewDatasetUploadFileModal
          open={fileOpen}
          onClose={() => {
            setFileOpen(false);
          }}
          refreshGrid={() =>
            queryClient.invalidateQueries({ queryKey: ["develop"] })
          }
        />
      )}
    </Stack>
  );
};

SheetBody.propTypes = {
  initial: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  setDirty: PropTypes.func.isRequired,
};

let openSeq = 0;

/**
 * The Add-data sheet (R2-SPEC P5): import sessions, traces, annotation
 * queue items or simulation runs into a dataset; preview, map, commit to
 * pending changes. Mounted ONCE inside DashboardLayout
 * (routes/sections/dashboard.jsx); opened only through openAddData(...).
 *
 * Props (frozen, R2 manifest row 25): none; it reads useAddDataStore
 * `{open, source, target, shape, close}`.
 */
const AddDataSheet = () => {
  const open = useAddDataStore((s) => s.open);
  const source = useAddDataStore((s) => s.source);
  const target = useAddDataStore((s) => s.target);
  const shape = useAddDataStore((s) => s.shape);
  const close = useAddDataStore((s) => s.close);
  const narrow = useMediaQuery("(max-width:1279.95px)");
  const [confirm, setConfirm] = useState(false);
  const [confirmSessions, setConfirmSessions] = useState(0);
  const dirtyRef = useRef(false);
  const selectedRef = useRef(0);
  const snap = useRef(null);
  if (
    open &&
    (!snap.current ||
      snap.current.source !== source ||
      snap.current.target !== target ||
      snap.current.shape !== shape ||
      !snap.current.open)
  ) {
    openSeq += 1;
    snap.current = { source, target, shape, open: true, key: openSeq };
  }
  if (!open && snap.current?.open)
    snap.current = { ...snap.current, open: false };

  // `escape` = Escape or a backdrop click: with sessions selected it asks
  // `Discard N selected sessions?` (IT-R5 G5 P7). Cancel stays a direct
  // decision and only asks when mappings or edits would be lost.
  const requestClose = (force, escape = false) => {
    const sessions = escape ? selectedRef.current : 0;
    if (!force && (dirtyRef.current || sessions > 0)) {
      setConfirmSessions(sessions);
      setConfirm(true);
    } else {
      dirtyRef.current = false;
      selectedRef.current = 0;
      close();
    }
  };

  const Body = gridWritesClassic() ? ProductionDataSheet : SheetBody;
  return (
    <>
      <Drawer
        anchor="right"
        open={open}
        onClose={() => requestClose(false, true)}
        PaperProps={{
          sx: {
            width: narrow ? "100%" : SHEET_WIDTH,
            maxWidth: "100%",
            height: "100vh",
            backgroundColor: "background.paper",
          },
        }}
      >
        {snap.current ? (
          <Body
            key={snap.current.key}
            initial={snap.current}
            onClose={(fromButton) => requestClose(!fromButton)}
            setDirty={(d, sessions = 0) => {
              dirtyRef.current = d;
              selectedRef.current = sessions;
            }}
          />
        ) : null}
      </Drawer>
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={
          confirmSessions > 0
            ? `Discard ${confirmSessions} selected ${
                confirmSessions === 1 ? "session" : "sessions"
              }?`
            : "Discard this import?"
        }
        content={
          confirmSessions > 0
            ? "They are not added to the dataset yet. Cancel keeps editing."
            : "Your mapping changes and edits will be lost."
        }
        action={
          <Button
            variant="contained"
            color="error"
            onClick={() => {
              setConfirm(false);
              requestClose(true);
            }}
          >
            Discard
          </Button>
        }
      />
    </>
  );
};

export default AddDataSheet;
