import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Skeleton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import React, {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate } from "react-router";
import { handedDeleteOf } from "./deleteHandoff";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import { DataTable, DataTablePagination } from "src/components/data-table";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  trackEvent,
  Events,
  PropertyName,
  handleOnDocsClicked,
} from "src/utils/Mixpanel";
import axios, { endpoints } from "src/utils/axios";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import ConfigureDatasetModal from "src/sections/develop-detail/Common/ConfigureDatasetModal";
import DatasetSettingsDialog from "./organize/DatasetSettingsDialog";
import BaseColumnDrawer from "../develop-detail/BaseColumnsDrawer";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { ViewMenu } from "src/sections/common/ux/oneview/ViewMenu";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { KeyCap } from "src/sections/common/ux/KeyCap";
import EmptyState from "src/sections/common/ux/EmptyState";
import ErrorState from "src/sections/common/ux/ErrorState";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { openShortcutSheet } from "src/sections/common/ux/useShortcuts";
import { fmtCount } from "src/sections/common/format/fmt";
import { shortId } from "src/sections/common/ux/CopyId";
// GS-06 (options-truth L3 G10): the Add dataset drawer (manual rows, the
// Data tab's cell renderers, markdown) loads when it first opens.
const AddDatasetDrawer = React.lazy(
  () => import("./AddDatasetDrawer/AddDatasetDrawer"),
);
import DuplicateDataset from "./DuplicateDataset/DuplicateDataset";
import { DatasetExportDialog } from "src/sections/common/export/ExportDialog";
import { RowMenuList } from "./DatasetListCells";
import { isPrototypeMode } from "src/utils/prototype";
import { createDoorShown, offeredImportOptions } from "./importOptions";
import ListSelectionBar from "./ListSelectionBar";
import { datasetListColumns } from "./datasetListColumns";
import {
  DATASET_COLUMN_GROUPS,
  DATASET_LIST_SURFACE,
  datasetListCatalog,
  deleteConfirmCopy,
  LIST_URL_DEFAULTS,
  PAGE_SIZES,
  sortingFromParam,
  sortParamFrom,
} from "./datasetList";
// the interface contract (the interface contract §5, PRIMITIVES-the interface contract.md § Mount): the list query (filter,
// Group by, starred first), the folder rail, the star and the organise doors.
import {
  queryErrorSentence,
  useDatasetListQuery,
} from "./hooks/useDatasetListQuery";
import useDatasetStars from "./hooks/useDatasetStars";
import useGroupCollapse from "./hooks/useGroupCollapse";
import useDatasetOrganize from "./organize/useDatasetOrganize";
import useFolderActions from "./organize/useFolderActions";
import { useRealFolderInventory } from "./organize/realFolderApi";
import { folderPickAfterMutation } from "./organize/folderSelection";
import {
  DatasetFilterChips,
  DatasetListControls,
} from "./organize/DatasetListControls";
import FolderRail from "./organize/FolderRail";
import FolderBreadcrumb from "./organize/FolderBreadcrumb";
import { FolderDndProvider } from "./organize/FolderDnd";
import { railCounts, railPick, withRailPick } from "./organize/folderRailModel";
import { tableGroups } from "./organize/groupFilter";
import { datasetOrgColumns } from "./organize/orgColumns";
import {
  applySavedListState,
  filtersFromUrl,
  groupColumnOf,
  groupOfColumn,
  listFields,
  matchLine,
  savedListState,
  urlFromFilters,
} from "./organize/datasetListView";
import { errorSentence, useDatasetStar } from "src/api/develop/dataset-org";
// L1 (the capability contract): what the real backend serves (get-datasets list,
// immediate writes); everything else on this page is hidden there.
import { REAL_ROW_ACTIONS, datasetCaps } from "./realBackendCaps";
import {
  cloneDatasetClassic,
  refusalSentence,
} from "src/sections/develop-detail/DataTab/Versions/classicWrites";

// IT-R4 G2 P6: the tab DatasetHeaderBar remembered for a dataset, or null.
const LAST_TABS = ["data", "experiments", "summary", "optimization"];
function lastTabOf(datasetId) {
  try {
    const t = window.localStorage.getItem(`dsx.lastTab.${datasetId}`);
    return LAST_TABS.includes(t) ? t : null;
  } catch {
    return null;
  }
}

const RESTORE_KEY = "fa.datasets.list.restore";
const UNDO_MS = 10000;

const readJson = (store, key, fallback) => {
  try {
    const raw = window[store].getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
};
const writeJson = (store, key, value) => {
  try {
    window[store].setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable: this page only
  }
};

// One bar per visible column after Name (A1-06).
// eslint-disable-next-line react/prop-types
const ListSkeleton = ({ bars }) => (
  <Box sx={{ px: 1 }} aria-busy="true" aria-label="Loading datasets">
    {[...Array(6)].map((_, i) => (
      <Stack
        key={i}
        direction="row"
        alignItems="center"
        gap={3}
        sx={{ height: 56, borderBottom: 1, borderColor: "divider" }}
      >
        <Skeleton variant="rounded" width={18} height={18} />
        <Box sx={{ flex: 2, minWidth: 260 }}>
          <Skeleton width="45%" height={18} />
          <Skeleton width="70%" height={14} />
        </Box>
        {[...Array(Math.max(1, bars))].map((_, j) => (
          <Skeleton key={j} sx={{ flex: 0.6 }} height={16} />
        ))}
      </Stack>
    ))}
  </Box>
);
const DevelopView = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { role } = useAuthContext();
  const caps = datasetCaps();

  // URL = view state (DL#13): ?q=&sort=&page=&size= (replace).
  const [view, setView] = useViewUrlState({
    ...LIST_URL_DEFAULTS,
    starred: false,
  });
  const starredOnly = caps.real && caps.listStars && view.starred;
  const [searchInput, setSearchInput] = useState(view.q);
  const searchRef = useRef(null);
  useEffect(() => {
    // Back / pasted link: the field follows the URL.
    setSearchInput((cur) => (cur.trim() === view.q ? cur : view.q));
  }, [view.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      const q = searchInput.trim();
      if (q !== view.q) setView({ q, page: 0 });
    }, 250);
    return () => clearTimeout(t);
  }, [searchInput]); // eslint-disable-line react-hooks/exhaustive-deps

  const sorting = useMemo(() => sortingFromParam(view.sort), [view.sort]);
  const sortBy = sorting[0] ? sortParamFrom(sorting).split(":")[0] : null;
  const sortOrder = sorting[0]?.desc ? "desc" : "asc";

  // Selection by id (survives sort/page; restored on Back).
  const restore = useRef(null);
  if (restore.current === null) {
    // One-shot: Back restores selection + scroll once, then forgets it.
    restore.current = readJson("sessionStorage", RESTORE_KEY, false);
    try {
      window.sessionStorage.removeItem(RESTORE_KEY);
    } catch {
      // storage unavailable
    }
  }
  const [selectedIds, setSelectedIds] = useState(
    () => restore.current?.selected ?? [],
  );
  const [activeId, setActiveId] = useState(null);
  const [pendingDelete, setPendingDelete] = useState([]); // hidden until undo window ends
  const [confirmItems, setConfirmItems] = useState(null);
  const [duplicateTarget, setDuplicateTarget] = useState(null);
  const [editTarget, setEditTarget] = useState(null);
  // the interface contract: the dataset `Download…` dialog.
  const [exportTarget, setExportTarget] = useState(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState(null); // {row, x, y}
  const [addOpen, setAddOpen] = useState(false);
  const [addStart, setAddStart] = useState(null); // {path, name}
  const tableBoxRef = useRef(null);

  // the interface contract, A3: the structured filter (URL `f`) and Group by (URL `group`)
  // ride the list query; starred datasets come first (server order).
  // L1: the real backend filters nothing (get-datasets takes no filter):
  // a stale `f=` link is ignored there, never sent.
  const { filters, error: linkFilterError } = useMemo(() => {
    if (caps.listFilters) return filtersFromUrl(view.f);
    if (caps.listFolders) {
      const parsed = filtersFromUrl(view.f);
      return {
        ...parsed,
        filters: parsed.filters.filter(
          (filter) =>
            filter.column_id === "folder" &&
            ["starts_with", "is_null"].includes(
              filter.filter_config?.filter_op,
            ),
        ),
      };
    }
    return { filters: [], error: null };
  }, [view.f, caps.listFilters, caps.listFolders]);
  const setFilters = useCallback(
    (next) => setView({ f: urlFromFilters(next), page: 0 }),
    [setView],
  );
  const { data, isLoading, isError, error, refetch } = useDatasetListQuery({
    page: view.page,
    pageSize: view.size,
    search: view.q || null,
    sortBy,
    sortOrder,
    filters: view.f,
    groupBy: view.group,
    starred: starredOnly,
    folderPath:
      railPick(filters).kind === "folder"
        ? railPick(filters).path
        : railPick(filters).kind === "none"
          ? ""
          : undefined,
  });
  const folderInventory = useRealFolderInventory(caps.real && caps.listFolders);
  // A refused query (a stale link's condition) keeps the page: the chips
  // stay so the condition can be removed, and the sentence says why.
  const refused = queryErrorSentence(error);
  // The rail counts the list without its own conditions (folder, Starred).
  const railRest = useMemo(
    () => withRailPick(filters, { kind: "all" }),
    [filters],
  );
  const railOwn = railRest.length !== filters.length;
  const railQ = useDatasetListQuery(
    { pageSize: 1, search: view.q || null, filters: urlFromFilters(railRest) },
    { enabled: railOwn },
  );
  const railData =
    caps.real && caps.listFolders
      ? {
          folders: folderInventory.data?.folders ?? [],
          total: folderInventory.data?.all_count ?? 0,
          facets: {
            starred: [
              { value: true, count: folderInventory.data?.starred_count ?? 0 },
            ],
          },
        }
      : railOwn
        ? railQ.data
        : data;
  // The footer's `2 of 9 datasets match`: the same search, no conditions.
  const allQ = useDatasetListQuery(
    { pageSize: 1, search: view.q || null },
    { enabled: filters.length > 0 && railRest.length > 0 },
  );
  const unfilteredTotal = railRest.length ? allQ.data?.total : railData?.total;
  const pick =
    caps.real && starredOnly ? { kind: "starred" } : railPick(filters);
  // The old per-browser pins become server stars, once.
  useDatasetStars({
    enabled: !caps.real && !isLoading && caps.listStars,
    onDone: () =>
      queryClient.invalidateQueries({ queryKey: ["datasets", "query"] }),
  });

  const fields = useMemo(
    () => listFields(data?.facets, data?.metadataTypes),
    [data?.facets, data?.metadataTypes],
  );
  const groupBy =
    caps.listGroupBy && fields.some((f) => f.id === view.group)
      ? view.group
      : "";
  const grouped = Boolean(groupBy) && Array.isArray(data?.groups);
  const visibleGroups = useMemo(
    () =>
      grouped
        ? data.groups.map((g) => ({
            ...g,
            datasets: g.datasets.filter((d) => !pendingDelete.includes(d.id)),
          }))
        : null,
    [grouped, data, pendingDelete],
  );
  // Grouped, a dataset can sit in several groups: the rows are each once.
  const items = useMemo(() => {
    const source = visibleGroups
      ? visibleGroups.flatMap((g) => g.datasets)
      : (data?.items ?? []).filter((d) => !pendingDelete.includes(d.id));
    const seen = new Set();
    return source.filter((d) => !seen.has(d.id) && seen.add(d.id));
  }, [visibleGroups, data, pendingDelete]);
  const [collapsedGroups, toggleGroup] = useGroupCollapse(groupBy);

  // Columns like Excel (A1-01/02): default set fits 1280; the rest is one
  // click away behind `6 hidden`; remembered per user. the interface contract: + Folder,
  // Agents, Metadata and one per metadata key (all hidden by default).
  const metaKeys = Object.keys(
    data?.metadataTypes ?? data?.facets?.metadata ?? {},
  ).join("\u0000");
  const metadataKeys = useMemo(
    () => (metaKeys ? metaKeys.split("\u0000") : []),
    [metaKeys],
  );
  const layout = useColumnLayout({
    surface: DATASET_LIST_SURFACE,
    columns: datasetListCatalog(metadataKeys),
  });
  const total = Math.max(0, (data?.total ?? 0) - pendingDelete.length);
  const byId = useMemo(
    () => Object.fromEntries(items.map((d) => [d.id, d])),
    [items],
  );
  const selectedItems = useMemo(
    () => selectedIds.map((id) => byId[id]).filter(Boolean),
    [selectedIds, byId],
  );

  // Restore list scroll after Back (DL#13).
  useEffect(() => {
    const top = restore.current?.scrollTop;
    if (!top || !items.length) return;
    const scroller = tableBoxRef.current?.querySelector(
      ".MuiDataGrid-virtualScroller",
    );
    if (scroller) scroller.scrollTop = top;
    restore.current = null;
  }, [items.length]);

  const canCreate = Boolean(RolePermission.DATASETS[PERMISSIONS.CREATE][role]);
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const canDelete = Boolean(RolePermission.DATASETS[PERMISSIONS.DELETE][role]);

  const refreshGrid = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
    queryClient.invalidateQueries({ queryKey: ["datasets", "query"] });
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-name-list"],
    });
  }, [queryClient]);

  const openDataset = useCallback(
    (row, { newTab = false } = {}) => {
      // IT-R2 G2 P1: a dataset with experiments opens where its answer is
      // (the Experiments tab); one without any opens on its data.
      // IT-R4 G2 P6: …unless it was left on another tab (DatasetHeaderBar).
      // L1: on the real backend a dataset opens on its Data tab (the
      // Experiments tab is not part of the Datasets launch).
      const tab = caps.openOnExperimentsTab
        ? lastTabOf(row.id) ??
          (Number(row.numberOfExperiments) > 0 ? "experiments" : "data")
        : "data";
      const href = `/dashboard/develop/${row.id}?tab=${tab}`;
      if (newTab) {
        window.open(href, "_blank", "noopener");
        return;
      }
      const scroller = tableBoxRef.current?.querySelector(
        ".MuiDataGrid-virtualScroller",
      );
      writeJson("sessionStorage", RESTORE_KEY, {
        selected: selectedIds,
        scrollTop: scroller?.scrollTop ?? 0,
      });
      trackEvent(Events.datasetOpened, {
        [PropertyName.id]: row.id,
        [PropertyName.name]: row.name,
      });
      navigate(href, { state: { dataset: row } });
    },
    [navigate, selectedIds, caps.openOnExperimentsTab],
  );

  // L1: Duplicate on the real backend is dev's clone, sent as dev reads it
  // (no dry run: dev does not know it and would answer with a real copy),
  // so it copies at once and says where the copy is.
  const duplicateNow = useCallback(
    async (row) => {
      if (!row) return;
      try {
        const res = await cloneDatasetClassic(row.id);
        const r = res?.data?.result ?? {};
        refreshGrid();
        notify(`Duplicated "${row.name}" as "${r.dataset_name ?? "a copy"}"`, {
          variant: "success",
          key: "dataset-duplicate",
        });
      } catch (e) {
        notify(refusalSentence(e, `Couldn't duplicate "${row.name}".`), {
          variant: "error",
          key: "dataset-duplicate",
        });
      }
    },
    [refreshGrid],
  );
  const startDuplicate =
    caps.real && !caps.duplicateMove ? duplicateNow : setDuplicateTarget;

  const openAdd = useCallback((start = null) => {
    trackEvent(Events.addDatasetCLicked);
    setAddStart(start);
    setAddOpen(true);
  }, []);

  // Delete: confirm by name with counts, hide at once, commit after the
  // 10 s Undo window (DL#45, F6).
  const commitDelete = useCallback(
    (rows) => {
      const ids = rows.map((r) => r.id);
      setPendingDelete((p) => [...p, ...ids]);
      setSelectedIds((s) => s.filter((id) => !ids.includes(id)));
      const label =
        rows.length === 1
          ? `Deleted "${rows[0].name}"`
          : `Deleted ${fmtCount(rows.length, "dataset")}`;
      const toast = notify(label, {
        key: `dataset-delete:${ids.join(",")}`,
        undo: { onUndo: () => {}, ms: UNDO_MS },
      });
      toast.undone.then(async (undone) => {
        if (undone) {
          setPendingDelete((p) => p.filter((id) => !ids.includes(id)));
          notify(
            rows.length === 1
              ? `Restored "${rows[0].name}"`
              : `Restored ${fmtCount(rows.length, "dataset")}`,
            { variant: "success", key: "dataset-restore" },
          );
          return;
        }
        try {
          await axios.delete(endpoints.develop.deleteDataset(), {
            data: { dataset_ids: ids },
          });
          trackEvent(Events.datasetDeleteClicked);
        } catch {
          notify("Couldn't delete. The dataset is back in the list.", {
            variant: "error",
            key: "dataset-delete-failed",
          });
        } finally {
          setPendingDelete((p) => p.filter((id) => !ids.includes(id)));
          refreshGrid();
        }
      });
    },
    [refreshGrid],
  );

  // v2.1 g8 (header:more): the dataset page's Delete hands its dataset
  // here (router state), so it goes through the same hide + Undo window.
  const location = useLocation();
  const handedDelete = useMemo(
    () => handedDeleteOf(location.state),
    [location.state],
  );
  useEffect(() => {
    if (!handedDelete) return;
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    });
    commitDelete(handedDelete);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handedDelete]);

  const { mutateAsync: setStar } = useDatasetStar();
  // The row menu's organise items reach the organise hook (declared below,
  // since it needs the selection) through this ref.
  const organizeRef = useRef(null);
  const allRowActions = useMemo(
    () => ({
      open: (row) => openDataset(row),
      openExperiments: (row) =>
        navigate(`/dashboard/develop/${row.id}?tab=experiments`),
      newExperiment: (row) => {
        useRunExperimentStore.getState().initiateCreateMode({ prefill: {} });
        navigate(`/dashboard/develop/${row.id}?tab=data`);
      },
      edit: (row) => setEditTarget(row),
      duplicate: (row) => startDuplicate(row),
      download: (row) => setExportTarget(row),
      copyLink: async (row) => {
        const ok = await copyText(
          `${window.location.origin}/dashboard/develop/${row.id}?tab=data`,
        );
        notify(ok ? `Link to "${row.name}" copied` : "Couldn't copy the link", {
          variant: ok ? "success" : "error",
          key: "copy-link",
        });
      },
      copyId: async (row) => {
        const ok = await copyText(row.id);
        notify(
          ok ? `Dataset id copied · ${shortId(row.id)}` : "Couldn't copy",
          {
            variant: ok ? "success" : "error",
            key: "copy-id",
          },
        );
      },
      remove: (row) => setConfirmItems([row]),
      // the interface contract transfer H1: Star / Unstar and the organise dialogs.
      star: async (row) => {
        try {
          await setStar({ id: row.id, on: !row.starred });
          notify(`${row.starred ? "Unstarred" : "Starred"} "${row.name}"`, {
            variant: "success",
            key: "dataset-star",
          });
        } catch (e) {
          notify(errorSentence(e), { variant: "error", key: "dataset-star" });
        }
      },
      organize: (kind, row) => organizeRef.current?.(kind, row),
    }),
    [navigate, openDataset, setStar, startDuplicate],
  );
  // L1: the real backend gets only the actions it serves (the row menu
  // shows nothing else). MAIN-RULING-EXPORT-CONTRACT keeps ordinary CSV's
  // callback in every build; the row menu still enforces download access.
  const rowActions = useMemo(
    () =>
      caps.real
        ? Object.fromEntries(
            [
              ...REAL_ROW_ACTIONS,
              ...(caps.listStars ? ["star"] : []),
              ...(caps.settings ? ["edit"] : []),
              ...(caps.listOrganize ? ["organize"] : []),
            ].map((key) => [key, allRowActions[key]]),
          )
        : allRowActions,
    [
      caps.real,
      caps.listStars,
      caps.settings,
      caps.listOrganize,
      allRowActions,
    ],
  );

  // the interface contract, A6, A7: `Organize ▾`, ⌘K and the row menu open the same dialogs
  // on the selection (else the active row); folder actions plan over the
  // folder's whole subtree.
  const organize = useDatasetOrganize({
    selected: selectedItems,
    active: byId[activeId] ?? null,
    canEdit,
    folders: railData?.folders ?? data?.folders ?? [],
  });
  // A row in the selection acts on the selection; any other row on itself.
  organizeRef.current = (kind, row) =>
    organize.openFor(kind, selectedIds.includes(row.id) ? undefined : [row]);
  const folderActions = useFolderActions(railData?.folders ?? [], (change) => {
    const current = railPick(filters);
    const next = folderPickAfterMutation(current, change);
    if (next !== current) setFilters(withRailPick(filters, next));
  });
  const railShown =
    caps.listFolders && (caps.real || Boolean(railData?.folders?.length));

  const columns = useMemo(
    () =>
      datasetListColumns({
        query: view.q,
        rowActions,
        canEdit,
        canDelete,
        draggable: canEdit && railShown,
        extra: caps.real ? [] : datasetOrgColumns({ metadataKeys }),
        real: caps.real,
      }),
    [
      view.q,
      rowActions,
      canEdit,
      canDelete,
      railShown,
      metadataKeys,
      caps.real,
    ],
  );

  // Keyboard (DL#19, DL#49): J/K move, Enter opens, X selects, N new, / search.
  const move = (step) => {
    if (!items.length) return;
    const i = items.findIndex((d) => d.id === activeId);
    const next =
      i < 0
        ? step > 0
          ? 0
          : items.length - 1
        : Math.max(0, Math.min(items.length - 1, i + step));
    setActiveId(items[next].id);
    // Grouped, a grid row's id is `<group>::<dataset id>`.
    const el = tableBoxRef.current?.querySelector(
      `[data-id="${items[next].id}"], [data-id$="::${items[next].id}"]`,
    );
    el?.scrollIntoView?.({ block: "nearest" });
  };
  const toggleSelect = (id) =>
    setSelectedIds((s) =>
      s.includes(id) ? s.filter((x) => x !== id) : [...s, id],
    );
  useShortcuts("list", {
    next: () => move(1),
    prev: () => move(-1),
    open: () => byId[activeId] && openDataset(byId[activeId]),
    select: () => activeId && toggleSelect(activeId),
    newDataset: () => canCreate && openAdd(),
    duplicate: () =>
      byId[activeId] && canEdit && startDuplicate(byId[activeId]),
    search: () => searchRef.current?.focus(),
  });
  useShortcuts(
    "global",
    { escape: () => setSelectedIds([]) },
    { enabled: selectedIds.length > 0, register: false },
  );

  useRegisterCommands(
    "datasets-list",
    [
      {
        id: "datasets.new",
        label: "New dataset",
        keywords: ["create", "add"],
        shortcut: "newDataset",
        icon: "mdi:plus",
        run: () => openAdd(),
        when: () => canCreate,
      },
      {
        id: "datasets.upload",
        label: "Upload a file",
        keywords: ["csv", "json", "jsonl", "import"],
        icon: "mdi:file-upload-outline",
        run: () => openAdd({ path: "uploadFile" }),
        when: () => canCreate,
      },
      {
        id: "datasets.fromTraces",
        label: "New dataset from production traces",
        keywords: ["observe", "sessions", "import"],
        icon: "mdi:transit-connection-variant",
        run: () => openAdd({ path: "fromProduction" }),
        // L1: not offered on the real backend (imports are not traced).
        when: () => canCreate && caps.listFromTracesCommand,
      },
    ],
    [canCreate, caps.listFromTracesCommand],
  );

  const selectionIndex = useMemo(() => {
    const o = {};
    items.forEach((d, i) => {
      if (selectedIds.includes(d.id)) o[i] = true;
    });
    return o;
  }, [items, selectedIds]);

  const searching = Boolean(view.q);
  const filtered = filters.length > 0;
  const noDatasetsAtAll =
    !isLoading &&
    !isError &&
    total === 0 &&
    !searching &&
    !filtered &&
    !starredOnly &&
    !pendingDelete.length;

  const newButton = (
    <KeyHintTooltip
      title={canCreate ? "New dataset" : "Viewers can't create datasets"}
      action="newDataset"
    >
      <span>
        <Button
          variant="contained"
          color="primary"
          disabled={!canCreate}
          startIcon={<Iconify icon="mingcute:add-line" width={18} />}
          onClick={() => openAdd()}
          sx={{ px: 2, typography: "s1", textTransform: "none", height: 36 }}
        >
          New dataset
        </Button>
      </span>
    </KeyHintTooltip>
  );

  const [addEverOpened, setAddEverOpened] = useState(false);
  useEffect(() => {
    if (addOpen) setAddEverOpened(true);
  }, [addOpen]);
  const addDrawer = addEverOpened ? (
    <Suspense fallback={null}>
      <AddDatasetDrawer
        open={addOpen}
        startWith={addStart}
        onClose={() => {
          setAddOpen(false);
          setAddStart(null);
        }}
        refreshGrid={refreshGrid}
      />
    </Suspense>
  ) : null;

  if (noDatasetsAtAll && !caps.listFolders) {
    return (
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <EmptyLayout
          title="Add your first dataset"
          description={
            createDoorShown("manuallyCreateDataset") &&
            createDoorShown("fromProduction")
              ? "A dataset holds the tasks you test your prompt or agent on. Upload a file, write rows by hand or import production traces."
              : offeredImportOptions().some(
                    (option) =>
                      option.id === "importFromHuggingface" && !option.disabled,
                  )
                ? "A dataset holds the tasks you test your prompt or agent on. Upload a file, copy rows from another dataset or import one from Hugging Face."
                : "A dataset holds the tasks you test your prompt or agent on. Upload a file or start with a blank grid."
          }
          link="https://docs.futureagi.com/docs/dataset"
          linkText="Read the datasets guide"
          onLinkClick={() => handleOnDocsClicked("datasets_main_page")}
          action={newButton}
          icon="/assets/icons/navbar/hugeicons.svg"
        />
        {addDrawer}
      </Box>
    );
  }

  const confirm = confirmItems ? deleteConfirmCopy(confirmItems) : null;
  // Grouped, each group shows its first datasets and `Show all n`: no pager.
  const showPager = !grouped && (total > view.size || view.page > 0);
  const counts = railCounts(railData);
  const pickRail = (next) =>
    setView({
      f: urlFromFilters(
        withRailPick(
          filters,
          caps.real && next.kind === "starred" ? { kind: "all" } : next,
        ),
      ),
      ...(caps.real ? { starred: next.kind === "starred" } : {}),
      page: 0,
    });

  return (
    <Box
      sx={{
        position: "relative",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {/* Toolbar: fixed height, selection never shifts it (DL#16). */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          minHeight: 40,
          flexShrink: 0,
        }}
      >
        <TextField
          inputRef={searchRef}
          size="small"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              if (searchInput) setSearchInput("");
              else e.currentTarget.querySelector("input")?.blur();
            }
          }}
          placeholder={
            caps.searchScope === "name"
              ? "Search by name"
              : "Search name, tag or column"
          }
          inputProps={{ "aria-label": "Search datasets" }}
          sx={{
            width: 300,
            "& .MuiOutlinedInput-root": { height: 34, typography: "s2" },
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="eva:search-fill"
                  sx={{ color: "text.secondary" }}
                />
              </InputAdornment>
            ),
            endAdornment: (
              <InputAdornment position="end">
                {searchInput ? (
                  <IconButton
                    size="small"
                    aria-label="Clear search"
                    onClick={() => {
                      setSearchInput("");
                      searchRef.current?.focus();
                    }}
                  >
                    <Iconify icon="mingcute:close-line" width={16} />
                  </IconButton>
                ) : (
                  <KeyCap>/</KeyCap>
                )}
              </InputAdornment>
            ),
          }}
        />
        {/* the interface contract, A3: Filter datasets and Group by (L1: not on the real
            backend, whose list takes no filter or grouping). */}
        {caps.real && caps.listStars && (
          <Button
            aria-pressed={starredOnly}
            onClick={() => setView({ starred: !starredOnly, page: 0 })}
          >
            Starred
          </Button>
        )}
        {caps.listFilters && (
          <DatasetListControls
            filters={filters}
            onFilters={setFilters}
            group={groupBy}
            onGroup={(group) => setView({ group, page: 0 })}
            facets={data?.facets}
            metadataTypes={data?.metadataTypes}
          />
        )}
        {/* IT-R5 G2 S4: one `View` (columns and the hidden count inside),
            not `Columns` plus a `7 hidden` chip. the interface contract: saved views of the
            list (search, sort, filter, Group by; never the page), shared
            with the workspace by editors. */}
        <ViewMenu
          surface={DATASET_LIST_SURFACE}
          columns={layout}
          groups={DATASET_COLUMN_GROUPS}
          sections={false}
          iconOnly={false}
          savedViews={
            caps.listSavedViews
              ? {
                  state: savedListState(view),
                  onApply: (state) => setView(applySavedListState(state)),
                  canShare: canEdit,
                }
              : undefined
          }
          isModified={filtered || Boolean(groupBy)}
          onReset={() => setView({ f: "", group: "", page: 0 })}
        />
        {pick.kind === "folder" && (
          <FolderBreadcrumb path={pick.path} onPick={pickRail} />
        )}
        <Box sx={{ flex: 1 }} />
        {caps.real && caps.listFolders && (
          <Button
            disabled={!canEdit}
            onClick={() => folderActions.open("create", "")}
          >
            New folder
          </Button>
        )}
        {newButton}
      </Box>

      {/* the interface contract: the conditions as chips, the left-out sentences, and why a
          condition was refused. */}
      {(filtered || refused || linkFilterError) && (
        <Box sx={{ flexShrink: 0 }}>
          <DatasetFilterChips
            filters={filters}
            onFilters={setFilters}
            facets={data?.facets}
            metadataTypes={data?.metadataTypes}
            leftOut={data?.leftOut}
          />
          {(refused || linkFilterError) && (
            <Typography
              role="alert"
              typography="s2"
              color="error.main"
              sx={{ mt: 0.5 }}
            >
              {refused ?? linkFilterError}
            </Typography>
          )}
        </Box>
      )}

      <FolderDndProvider selected={selectedItems} canEdit={canEdit}>
        <Box sx={{ flex: 1, minHeight: 0, display: "flex", gap: 1.5 }}>
          {/* the interface contract: the folder rail, while any dataset is in a folder. */}
          {railShown && (
            <Box sx={{ width: 208, flexShrink: 0, overflowY: "auto" }}>
              {caps.real && folderInventory.isError && (
                <ErrorState
                  thing="folders"
                  error={folderInventory.error}
                  onRetry={() => folderInventory.refetch()}
                />
              )}
              <FolderRail
                folders={railData.folders}
                {...counts}
                pick={pick}
                onPick={pickRail}
                onAction={folderActions.open}
                canEdit={canEdit}
              />
            </Box>
          )}
          <Box
            ref={tableBoxRef}
            sx={{
              flex: 1,
              minWidth: 0,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            {isError && !refused ? (
              <ErrorState
                thing="datasets"
                error={error}
                onRetry={() => refetch()}
              />
            ) : isLoading && !items.length ? (
              <ListSkeleton bars={layout.visibleIds.length - 1} />
            ) : (
              <DataTable
                ariaLabel="Datasets"
                columns={columns}
                data={items}
                rowCount={total}
                sorting={sorting}
                onSortingChange={(next) =>
                  setView({ sort: sortParamFrom(next), page: 0 })
                }
                rowSelection={selectionIndex}
                onRowSelectionChange={(obj) =>
                  setSelectedIds(
                    Object.keys(obj)
                      .filter((k) => obj[k])
                      .map((k) => items[Number(k)]?.id)
                      .filter(Boolean),
                  )
                }
                onRowClick={(row) => {
                  setActiveId(row.id);
                  openDataset(row);
                }}
                onRowContextMenu={(row, e) =>
                  setContextMenu({ row, x: e.clientX, y: e.clientY })
                }
                getRowClassName={(p) =>
                  p.row?.id === activeId ? "row-active" : ""
                }
                getRowId={(row) => row.id}
                layout={layout}
                headerMenu
                enableSelection
                selectOnHover
                rowHeight={56}
                // the interface contract: Group by as full-width group rows (`Show all n` on a
                // group larger than it shows); a column header's Group by is
                // the same setting.
                groups={tableGroups(visibleGroups, {
                  field: fields.find((f) => f.id === groupBy),
                  filters,
                  onFilters: setFilters,
                })}
                collapsedGroups={collapsedGroups}
                onToggleGroup={toggleGroup}
                group={groupColumnOf(groupBy)}
                onGroup={(columnId) =>
                  setView({ group: groupOfColumn(columnId), page: 0 })
                }
                groupNoun="dataset"
                emptyContent={
                  refused ? (
                    <EmptyState
                      compact
                      icon="mdi:filter-remove-outline"
                      title="This filter can't be answered"
                      body={refused}
                      actions={[
                        {
                          label: "Clear filters",
                          onClick: () => setFilters([]),
                        },
                      ]}
                    />
                  ) : starredOnly ? (
                    <EmptyState
                      compact
                      icon="mdi:star-outline"
                      title="No starred datasets match"
                      actions={[
                        {
                          label: "Show all datasets",
                          onClick: () => setView({ starred: false, page: 0 }),
                        },
                      ]}
                    />
                  ) : filtered ? (
                    <EmptyState
                      compact
                      icon="mdi:filter-outline"
                      title="No datasets match these filters"
                      actions={[
                        {
                          label: "Clear filters",
                          onClick: () => setFilters([]),
                        },
                      ]}
                    />
                  ) : searching ? (
                    <EmptyState
                      compact
                      icon="mdi:magnify"
                      title={`No datasets match "${view.q}"`}
                      body={
                        caps.searchScope === "name"
                          ? "Search looks at dataset names."
                          : "Search looks at names, descriptions, tags and column names."
                      }
                      actions={[
                        {
                          label: "Clear search",
                          onClick: () => setSearchInput(""),
                        },
                        ...(canCreate &&
                        createDoorShown("manuallyCreateDataset")
                          ? [
                              {
                                label: `Create dataset named "${view.q}"`,
                                icon: "mdi:plus",
                                onClick: () =>
                                  openAdd({
                                    path: "manuallyCreateDataset",
                                    name: view.q,
                                  }),
                              },
                            ]
                          : []),
                      ]}
                    />
                  ) : (
                    <EmptyState compact title="No datasets on this page" />
                  )
                }
              />
            )}
          </Box>
        </Box>
      </FolderDndProvider>

      {/* Footer: count + shortcut hint left, pager right (DL#43, DL#49). */}
      <Box
        data-toast-clear
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 2,
          minHeight: 40,
          borderTop: "1px solid",
          borderColor: "divider",
          px: 1,
          flexShrink: 0,
        }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
          aria-live="polite"
        >
          {filtered && unfilteredTotal != null
            ? matchLine(total, unfilteredTotal, true)
            : searching
              ? `${fmtCount(total, "dataset")} match "${view.q}"`
              : fmtCount(total, "dataset")}
        </Typography>
        <Button
          size="small"
          color="inherit"
          onClick={() => openShortcutSheet({ surface: "list" })}
          sx={{
            typography: "s3",
            color: "text.secondary",
            gap: 0.5,
            textTransform: "none",
          }}
        >
          Press <KeyCap>?</KeyCap> for shortcuts
        </Button>
        <Box sx={{ flex: 1 }} />
        {showPager && (
          <DataTablePagination
            page={view.page}
            pageSize={view.size}
            total={total}
            pageSizeOptions={PAGE_SIZES}
            onPageChange={(p) => setView({ page: p })}
            onPageSizeChange={(size) => setView({ size, page: 0 })}
          />
        )}
      </Box>

      <ListSelectionBar
        count={selectedItems.length}
        onDuplicate={() => startDuplicate(selectedItems[0])}
        // L7b absent-sweep: Compare datasets is absent in prototype builds
        // (its flow is not mocked); the real app keeps it.
        onCompare={isPrototypeMode() ? undefined : () => setCompareOpen(true)}
        onDelete={() => setConfirmItems(selectedItems)}
        onClear={() => setSelectedIds([])}
        canCreate={canCreate}
        canDelete={canDelete}
        organize={caps.listOrganize ? organize.menu : null}
      />
      {caps.listOrganize && organize.dialogs}
      {caps.listFolders && folderActions.dialog}

      {contextMenu && (
        <RowMenuList
          open
          row={contextMenu.row}
          actions={rowActions}
          canEdit={canEdit}
          canDelete={canDelete}
          onClose={() => setContextMenu(null)}
          anchorReference="anchorPosition"
          anchorPosition={{ top: contextMenu.y, left: contextMenu.x }}
        />
      )}

      {confirm && (
        <ConfirmDialog
          open
          title={confirm.title}
          body={confirm.body}
          items={confirmItems.length > 1 ? confirm.names : []}
          typedConfirm={confirm.typedConfirm}
          confirmLabel={confirm.confirmLabel}
          onConfirm={() => {
            const rows = confirmItems;
            setConfirmItems(null);
            commitDelete(rows);
          }}
          onClose={() => setConfirmItems(null)}
        />
      )}

      {addDrawer}
      <DatasetExportDialog
        open={Boolean(exportTarget)}
        onClose={() => setExportTarget(null)}
        datasetId={exportTarget?.id}
        datasetName={exportTarget?.name}
      />
      <DuplicateDataset
        open={Boolean(duplicateTarget)}
        onClose={() => {
          setDuplicateTarget(null);
          refreshGrid();
        }}
        refreshGrid={refreshGrid}
        selected={duplicateTarget}
      />
      {editTarget && caps.real && caps.settings && (
        <DatasetSettingsDialog
          datasetId={editTarget.id}
          onClose={() => setEditTarget(null)}
        />
      )}
      {editTarget && !caps.real && (
        <ConfigureDatasetModal
          open
          onClose={() => setEditTarget(null)}
          datasetId={editTarget.id}
          datasetName={editTarget.name}
          description={editTarget.description ?? ""}
          tags={editTarget.tags ?? []}
          title="Dataset settings"
          onSaved={refreshGrid}
        />
      )}
      {selectedItems.length > 1 && !isPrototypeMode() && (
        <BaseColumnDrawer
          baseColumnDrawerVisible={compareOpen}
          selectedDatasets={selectedItems.map((d) => d.id)}
          onBaseColumnDrawerClose={() => setCompareOpen(false)}
          selectedDatasetsValues={selectedItems.map((d) => d.name)}
          compareFromOutside={true}
        />
      )}
    </Box>
  );
};

export default DevelopView;
