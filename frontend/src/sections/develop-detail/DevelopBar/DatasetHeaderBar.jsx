import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Link,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { datasetCaps } from "src/sections/develop/realBackendCaps";
import {
  cloneDatasetClassic,
  refusalSentence,
} from "../DataTab/Versions/classicWrites";
import { useLocation, useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import {
  getDatasetQueryOptions,
  useDevelopDatasetList,
} from "src/api/develop/develop-detail";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import { ItemsMenu } from "src/sections/common/ux/oneview/MenuItems";
import DuplicateDataset from "src/sections/develop/DuplicateDataset/DuplicateDataset";
import ConfirmDialog from "src/sections/common/ux/ConfirmDialog";
import { deleteConfirmCopy } from "src/sections/develop/datasetList";
import { copyText, useCopiedFlash } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { shortId } from "src/sections/common/ux/CopyId";
import { useDatasetsList } from "src/sections/develop/hooks/useDatasetsList";
import SavedSourcesChip from "src/sections/common/AddData/SavedSourcesChip";
import KbChangesBanner from "src/sections/common/AddData/KbChangesBanner";
import DatasetSelectDropDown from "../DatasetSelectDropDown";
import CompareDatasetDrawer from "../CompareDatasetDrawer";
import BaseColumnDrawer from "../BaseColumnsDrawer";
import ConfigureDatasetModal from "../Common/ConfigureDatasetModal";
import DatasetSettingsDialog from "src/sections/develop/organize/DatasetSettingsDialog";
import { useDatasetDetails } from "src/sections/develop/organize/useDatasetDetails";
import DatasetVersionChip from "../DataTab/Versions/DatasetVersionChip";
import NewExperimentButton from "../Experiment/NewExperimentButton";
import { usePendingChangesGate } from "../DataTab/Versions/PendingChangesGate";
import {
  useDatasetOriginStore,
  useRunExperimentStore,
  useRunOptimizationStore,
} from "../states";
import ShareDatasetDialog from "./ShareDatasetDialog";
import { headerAccessDoors } from "./headerAccessDoors";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { deleteHandoffState } from "src/sections/develop/deleteHandoff";
import { useRequestDatasetAccess } from "src/api/develop/dataset-access";
import {
  REQUESTED_LINE,
  namesOr,
} from "src/sections/common/Access/accessModel";
import AuditDrawer from "src/sections/common/Audit/AuditDrawer";
import ScenariosLine from "./ScenariosLine";
import { isPrototypeMode } from "src/utils/prototype";
import { isSurfaceShown } from "src/launch/launchSwitch";

/**
 * Dataset header (DL#31, SVP-4.2; one view A1-14): breadcrumb `Datasets /
 * <full name>` (click the name to copy it, ▾ switches dataset), the version
 * chip (its menu holds Version history and Compare), then the SAME actions
 * on every tab (P7): `Share` (icon) · `New experiment` (outlined) · `⋯`
 * (Compare datasets, Optimize, Dataset settings, Duplicate, Copy id,
 * Delete). With room (≥ 1600 px) Compare / Optimize / Settings also show as
 * icons — the width decides, never the tab. Every action is in ⌘K.
 */
/** localStorage key of the tab a dataset was last left on (IT-R4 G2 P6). */
export const lastTabKey = (datasetId) => `dsx.lastTab.${datasetId}`;
const REMEMBERED_TABS = ["data", "experiments", "summary", "optimization"];

function rememberLastTab(datasetId, tab) {
  if (!datasetId || !REMEMBERED_TABS.includes(tab)) return;
  try {
    window.localStorage.setItem(lastTabKey(datasetId), tab);
  } catch {
    /* private window / blocked storage: today's rule applies */
  }
}

/** The ConfirmDialog props of the header's Delete (the list's copy). */
const headerDeleteProps = (row) => {
  const c = deleteConfirmCopy([row]);
  return {
    title: c.title,
    body: c.body,
    items: [],
    typedConfirm: c.typedConfirm,
    confirmLabel: c.confirmLabel,
  };
};

export default function DatasetHeaderBar({
  currentTab,
  setCurrentTab,
  setIsCompareDataset,
  baseColumn,
  setBaseColumn,
  setIsCommonColumn,
  setSelectedDatasets,
  setSelectedDatasetData,
  setCompareFromOutside,
}) {
  const { dataset } = useParams();
  const { real, settings: settingsReal, duplicateMove } = datasetCaps();
  const duplicateInDialog = !real || duplicateMove;
  const queryClient = useQueryClient();
  const duplicateInFlight = useRef(false);
  const [duplicating, setDuplicating] = useState(false);
  // IT-R4 G2 P6: a dataset opens where you left it (DevelopView reads it).
  useEffect(() => {
    rememberLastTab(dataset, currentTab);
  }, [dataset, currentTab]);
  const navigate = useNavigate();
  const location = useLocation();
  const { role } = useAuthContext();
  const roomy = useMediaQuery("(min-width:1600px)");
  const pickerRef = useRef(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [dialog, setDialog] = useState(null); // configure | share
  const [compareOpen, setCompareOpen] = useState(false);
  const [baseOpen, setBaseOpen] = useState(false);
  const [compareSel, setCompareSel] = useState({ ids: [], values: [] });
  const [copied, flash] = useCopiedFlash();
  const { setOpenRunOptimization } = useRunOptimizationStore();
  const { processingComplete } = useDatasetOriginStore();

  const { data: names } = useDevelopDatasetList();
  const { data: list } = useDatasetsList({ page: 0, pageSize: 100 });
  const item = useMemo(
    () => list?.items?.find((d) => d.id === dataset) ?? null,
    [list, dataset],
  );
  const { data: details } = useDatasetDetails(dataset, real);
  const name =
    details?.name ??
    item?.name ??
    names?.find((d) => d.datasetId === dataset)?.name ??
    "";
  const datasetOptions = useMemo(
    () => (names ?? []).map((d) => ({ label: d.name, value: d.datasetId })),
    [names],
  );

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );
  // The page-0 table read exists once the Data tab loaded; on Summary /
  // Experiments the list item's row count answers "has rows" (the same
  // header on every tab must not grey New experiment there).
  const isData = tableData
    ? Boolean(tableData?.data?.result?.table?.length)
    : Number(item?.numberOfDatapoints) > 0;
  const synthetic = Boolean(tableData?.data?.result?.synthetic_dataset);
  const blocked = !isData || (synthetic && !processingComplete);
  const canEdit = Boolean(RolePermission.DATASETS[PERMISSIONS.UPDATE][role]);
  const canCreate = Boolean(RolePermission.DATASETS[PERMISSIONS.CREATE][role]);
  // W4 §9 (M6): the dataset's access view (Delete needs Manage; New
  // experiment needs run, NewExperimentButton reads it too).
  const access = useDatasetAccess(dataset);
  const requestAccess = useRequestDatasetAccess(dataset);
  const doors = headerAccessDoors({
    roleDelete: RolePermission.DATASETS[PERMISSIONS.DELETE][role],
    access,
  });
  const canDelete = doors.canDelete;

  // Match the list's real-backend action: copy at once with the classic
  // clone, unless the workspace copy-and-move dialog is available.
  const duplicate = useCallback(async () => {
    if (!canCreate || !item || duplicateInFlight.current) return;
    if (duplicateInDialog) {
      setDialog("duplicate");
      return;
    }
    duplicateInFlight.current = true;
    setDuplicating(true);
    try {
      const response = await cloneDatasetClassic(item.id);
      queryClient.invalidateQueries({ queryKey: ["datasets", "list"] });
      queryClient.invalidateQueries({
        queryKey: ["develop", "dataset-name-list"],
      });
      notify(
        `Duplicated "${item.name}" as "${response?.data?.result?.dataset_name ?? "a copy"}"`,
        {
          variant: "success",
          key: "dataset-duplicate",
        },
      );
    } catch (error) {
      notify(refusalSentence(error, `Couldn't duplicate "${item.name}".`), {
        variant: "error",
        key: "dataset-duplicate",
      });
    } finally {
      duplicateInFlight.current = false;
      setDuplicating(false);
    }
  }, [canCreate, item, duplicateInDialog, queryClient]);

  const actions = [
    // L7b absent-sweep: Compare datasets is absent in prototype builds (its
    // flow is not mocked), so no menu item, inline button or Cmd+K command.
    ...(isPrototypeMode()
      ? []
      : [
          {
            id: "compare",
            label: "Compare datasets",
            icon: "mdi:compare-horizontal",
            tooltip:
              "Compare this dataset with up to 2 others, column by column",
            disabled: blocked,
            reason: "Add rows first",
            run: () => {
              trackEvent(Events.datasetCompareClicked, {
                [PropertyName.id]: dataset,
              });
              setCompareOpen(true);
            },
          },
        ]),
    {
      id: "optimize",
      label: "Optimize",
      icon: "mdi:auto-fix",
      tooltip: "Improve a prompt's accuracy on these rows",
      disabled: blocked || !canEdit,
      reason: !canEdit ? "Viewers can't start optimizations" : "Add rows first",
      run: () => setOpenRunOptimization(true),
    },
    // The DSX metadata editor is held independently; keep the legacy rename door.
    ...(real || isSurfaceShown("dataset.settings")
      ? [
          {
            id: "configure",
            label: "Dataset settings",
            short: "Settings",
            icon: "mdi:cog-outline",
            tooltip:
              settingsReal || !real ? "Name, description and tags" : "Name",
            disabled: !canEdit,
            reason: "Viewers can't change settings",
            run: () => setDialog("configure"),
          },
        ]
      : []),
    // L6: Share holds access, sharing policy and public links (absent on dev).
    ...(isSurfaceShown("dataset.share")
      ? [
          {
            id: "share",
            label: "Share",
            icon: "mdi:share-variant-outline",
            tooltip: "Share a link to this dataset",
            run: () => setDialog("share"),
          },
        ]
      : []),
  ];
  const copyId = async () => {
    const ok = await copyText(dataset);
    notify(ok ? `Dataset id copied · ${shortId(dataset)}` : "Couldn't copy", {
      variant: ok ? "success" : "error",
      key: "copy-id",
    });
  };

  const gate = usePendingChangesGate();
  useRegisterCommands(
    "dataset-header",
    [
      {
        id: "dataset.newExperiment",
        label: "Run an experiment on this dataset",
        keywords: ["new experiment", "compare", "run"],
        icon: "mdi:flask-outline",
        run: () => {
          if (currentTab !== "data") setCurrentTab?.("data");
          gate("experiment", () =>
            useRunExperimentStore
              .getState()
              .initiateCreateMode({ prefill: {} }),
          );
        },
        when: () => !blocked && canEdit && doors.canRun,
      },
      {
        id: "dataset.copyId",
        label: "Copy dataset id",
        icon: "mdi:content-copy",
        run: copyId,
      },
      {
        id: "dataset.duplicate",
        label: "Duplicate dataset",
        icon: "mdi:content-duplicate",
        run: duplicate,
        when: () => canCreate && Boolean(item) && !duplicating,
      },
      {
        id: "dataset.delete",
        label: "Delete dataset",
        icon: "mdi:delete-outline",
        run: () => setDialog("delete"),
        when: () => canDelete,
      },
      // L6: the audit log reads the absent /model-hub/audit/.
      ...(isSurfaceShown("dataset.audit")
        ? [
            {
              id: "dataset.auditLog",
              label: "Open the dataset's audit log",
              keywords: ["audit", "history", "who changed"],
              icon: "mdi:history",
              run: () => setDialog("audit"),
            },
          ]
        : []),
      ...actions.map((a) => ({
        id: `dataset.${a.id}`,
        label: a.label,
        icon: a.icon,
        run: a.run,
        when: () => !a.disabled,
      })),
    ],
    [
      dataset,
      blocked,
      canEdit,
      canCreate,
      canDelete,
      doors.canRun,
      currentTab,
      item,
      duplicate,
      duplicating,
    ],
  );

  const goList = () => {
    // Back to the list with its search/sort/scroll when we came from it.
    if (location.state?.dataset) navigate(-1);
    else navigate("/dashboard/develop");
  };

  const share = actions.find((a) => a.id === "share");
  const inlineActions = roomy ? actions.filter((a) => a.id !== "share") : [];
  const menuItems = [
    ...actions
      .filter((a) => a.id !== "share")
      .map((a) => ({
        id: a.id,
        label: a.label,
        icon: a.icon,
        secondary: a.disabled ? undefined : a.tooltip,
        disabled: a.disabled,
        reason: a.reason,
        run: a.run,
      })),
    // IT-R5 G2 S3 (S5): production failures in, from any tab — the Data
    // tab opens `From production traces` on `add=production`.
    // L6: production failures in is absent on dev (the import endpoints).
    ...(isSurfaceShown("dataset.fromProduction")
      ? [
          {
            id: "addProduction",
            label: "Add production failures",
            icon: "mdi:database-import-outline",
            secondary: "Failing production traces become rows",
            disabled: !canEdit,
            reason: "Viewers can't add rows",
            run: () =>
              navigate(`/dashboard/develop/${dataset}?tab=data&add=production`),
          },
        ]
      : []),
    { divider: true },
    {
      id: "duplicate",
      label: "Duplicate",
      icon: "mdi:content-duplicate",
      disabled: !canCreate || !item || duplicating,
      reason: !canCreate
        ? "Viewers can't create datasets"
        : duplicating
          ? "Duplicating…"
          : "Loading…",
      run: duplicate,
    },
    {
      id: "copyId",
      label: "Copy dataset id",
      icon: "mdi:content-copy",
      secondary: shortId(dataset),
      run: copyId,
    },
    // W4 A should-fix 2 (M6): a viewer asks for download here.
    // L6: access requests need the absent dataset access endpoints.
    ...(doors.requestDownload && isSurfaceShown("dataset.share")
      ? [
          {
            id: "requestDownload",
            label: doors.requestDownload.requested
              ? "Download requested"
              : "Request download access",
            icon: "mdi:download-lock-outline",
            secondary: doors.requestDownload.requested
              ? REQUESTED_LINE
              : `Asks ${namesOr(access.ask) || "an admin"}`,
            disabled:
              doors.requestDownload.requested || requestAccess.isPending,
            reason: REQUESTED_LINE,
            run: () =>
              requestAccess.mutate("download", {
                onSuccess: () =>
                  notify(REQUESTED_LINE, { key: "request-download" }),
              }),
          },
        ]
      : []),
    // W4-7 (§9 integration, M6): the dataset's audit log.
    ...(isSurfaceShown("dataset.audit")
      ? [
          {
            id: "auditLog",
            label: "Audit log",
            icon: "mdi:history",
            secondary: "Versions, labels, access, sharing and reviews",
            run: () => setDialog("audit"),
          },
        ]
      : []),
    {
      id: "delete",
      label: "Delete",
      icon: "mdi:delete-outline",
      danger: true,
      disabled: !canDelete,
      reason: doors.deleteReason,
      run: () => setDialog("delete"),
    },
  ];

  return (
    <>
      <Box
        sx={{
          pt: 2,
          px: 2,
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          minHeight: 52,
          "& .MuiButton-root": { whiteSpace: "nowrap", flexShrink: 0 },
        }}
      >
        <Box
          component="nav"
          aria-label="Breadcrumb"
          sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0 }}
        >
          <Link
            component="button"
            type="button"
            onClick={goList}
            typography="s1"
            color="text.secondary"
            underline="hover"
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              flexShrink: 0,
            }}
          >
            <Iconify icon="mdi:arrow-left" width={16} />
            Datasets
          </Link>
          <Typography typography="s1" color="text.disabled">
            /
          </Typography>
          {/* GS-01 (options-truth L3 G10): the dataset's name is the page's
              one h1 (Data, Experiments, Summary and voice tabs alike). */}
          <Box
            component="h1"
            sx={{ m: 0, minWidth: 0, display: "flex", font: "inherit" }}
          >
            <CustomTooltip
              show
              size="small"
              title={copied ? "Copied" : `${name} · click to copy the name`}
            >
              <Typography
                component="button"
                type="button"
                onClick={async () => {
                  if (await copyText(name)) flash();
                }}
                typography="s1"
                fontWeight={600}
                color="text.primary"
                noWrap
                sx={{
                  border: 0,
                  bgcolor: "transparent",
                  p: 0,
                  cursor: "copy",
                  minWidth: 0,
                  textAlign: "left",
                }}
                aria-label={`Dataset ${name}. Click to copy the name.`}
              >
                {name || "…"}
              </Typography>
            </CustomTooltip>
          </Box>
          {copied && (
            <Iconify
              icon="mdi:check"
              width={16}
              sx={{ color: "success.main", flexShrink: 0 }}
            />
          )}
          <CustomTooltip show size="small" title="Switch dataset">
            <IconButton
              ref={pickerRef}
              size="small"
              aria-label="Switch dataset"
              onClick={() => setPickerOpen(true)}
            >
              <Iconify icon="mdi:chevron-down" width={18} />
            </IconButton>
          </CustomTooltip>
          <DatasetSelectDropDown
            ref={pickerRef}
            open={pickerOpen}
            onClose={() => setPickerOpen(false)}
            fetchOptions={useDevelopDatasetList}
            searchPlaceholder="Search datasets"
            labelText="All datasets"
            onSelect={(v) =>
              navigate(
                `/dashboard/develop/${v.value}?tab=${currentTab || "data"}`,
              )
            }
          />
        </Box>
        {/* L6: versions, saved sources and KB changes read absent paths. */}
        {isSurfaceShown("dataset.versions") ? (
          <Box sx={{ flexShrink: 0 }}>
            <DatasetVersionChip />
          </Box>
        ) : null}
        {/* W3-5: saved import sources (renders nothing without one). */}
        {dataset && isSurfaceShown("dataset.imports.sources") ? (
          <Box sx={{ flexShrink: 0 }}>
            <SavedSourcesChip datasetId={dataset} />
          </Box>
        ) : null}
        {/* W3-8b: a cited knowledge-base passage changed (else nothing). */}
        {dataset && isSurfaceShown("dataset.imports.sources") ? (
          <KbChangesBanner datasetId={dataset} />
        ) : null}
        {item?.kind === "scenarios" && dataset ? (
          <ScenariosLine datasetId={dataset} />
        ) : null}
        <Box sx={{ flex: 1 }} />
        {inlineActions.map((a) => (
          <KeyHintTooltip
            key={a.id}
            title={a.disabled ? a.reason : a.label}
            description={a.disabled ? undefined : a.tooltip}
          >
            <span>
              <IconButton
                size="small"
                aria-label={a.label}
                disabled={a.disabled}
                onClick={a.run}
                sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
              >
                <Iconify icon={a.icon} width={18} />
              </IconButton>
            </span>
          </KeyHintTooltip>
        ))}
        {share ? (
          <KeyHintTooltip title={share.label} description={share.tooltip}>
            <IconButton
              size="small"
              aria-label={share.label}
              onClick={share.run}
              sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
            >
              <Iconify icon={share.icon} width={18} />
            </IconButton>
          </KeyHintTooltip>
        ) : null}
        {/* IT-R3 G2 P5: New experiment sits here on EVERY tab (the
            Experiments toolbar no longer carries a second one). */}
        <NewExperimentButton placement="header" disabled={blocked} />
        <CustomTooltip show size="small" title="More dataset actions">
          <IconButton
            size="small"
            aria-label="More dataset actions"
            aria-haspopup="menu"
            onClick={(e) => setMenuAnchor(e.currentTarget)}
            sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
          >
            <Iconify icon="mdi:dots-horizontal" width={18} />
          </IconButton>
        </CustomTooltip>
      </Box>

      <ItemsMenu
        open={Boolean(menuAnchor)}
        anchorEl={menuAnchor}
        onClose={() => setMenuAnchor(null)}
        items={menuItems}
        minWidth={260}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      />
      {dialog === "audit" && (
        <AuditDrawer
          open
          onClose={() => setDialog(null)}
          scope={`dataset:${dataset}`}
          title={name}
        />
      )}
      {dialog === "configure" && real && (
        <DatasetSettingsDialog
          datasetId={dataset}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "configure" && !real && (
        <ConfigureDatasetModal
          open
          onClose={() => setDialog(null)}
          datasetId={dataset}
          datasetName={name}
          description={item?.description ?? ""}
          tags={item?.tags ?? []}
          title="Dataset settings"
        />
      )}
      {duplicateInDialog && dialog === "duplicate" && item && (
        <DuplicateDataset
          open
          onClose={() => setDialog(null)}
          refreshGrid={() => {}}
          selected={item}
        />
      )}
      {dialog === "delete" && (
        // v2.1 g8 (header:more): the list's one delete: the counted
        // confirm (typed name when experiments use it), then the list
        // hides it with a 10 s Undo before the delete is sent.
        <ConfirmDialog
          open
          {...headerDeleteProps(item ?? { id: dataset, name })}
          onConfirm={() => {
            setDialog(null);
            navigate("/dashboard/develop", {
              state: deleteHandoffState({
                id: dataset,
                name: item?.name ?? name,
                numberOfExperiments: item?.numberOfExperiments ?? 0,
              }),
            });
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "share" && (
        <ShareDatasetDialog
          open
          onClose={() => setDialog(null)}
          datasetId={dataset}
          name={name}
        />
      )}
      {/* perf #6: mounted while open only; a closed drawer kept its
          loading skeleton in the page forever. */}
      {compareOpen && !isPrototypeMode() ? (
        <CompareDatasetDrawer
          compareDatasetDrawerVisible={compareOpen}
          onCompareDatasetDrawerClose={() => setCompareOpen(false)}
          setCompareDatasetDrawerVisible={setCompareOpen}
          datasetOptions={datasetOptions}
          setBaseColumnDrawerVisible={setBaseOpen}
          onSelectedDatasetsChange={(ids, values) => {
            setCompareSel({ ids, values });
            setSelectedDatasets(ids);
            setSelectedDatasetData(values);
          }}
        />
      ) : null}
      {compareSel.ids.length && !isPrototypeMode() ? (
        <BaseColumnDrawer
          setIsCompareDataset={(v) => {
            // DatasetsSelectRow takes over in compare mode; tell it the
            // selection came from here so its mount keeps it.
            if (v) setCompareFromOutside(true);
            setIsCompareDataset(v);
          }}
          baseColumnDrawerVisible={baseOpen}
          selectedDatasets={compareSel.ids}
          onBaseColumnDrawerClose={() => setBaseOpen(false)}
          onCompareDatasetDrawerClose={() => setCompareOpen(false)}
          selectedValue={baseColumn}
          setSelectedValue={setBaseColumn}
          setCurrentTab={setCurrentTab}
          setIsCommonColumn={setIsCommonColumn}
          selectedDatasetsValues={compareSel.values}
        />
      ) : null}
    </>
  );
}

DatasetHeaderBar.propTypes = {
  currentTab: PropTypes.string,
  setCurrentTab: PropTypes.func,
  setIsCompareDataset: PropTypes.func,
  baseColumn: PropTypes.string,
  setBaseColumn: PropTypes.func,
  setIsCommonColumn: PropTypes.func,
  setSelectedDatasets: PropTypes.func,
  setSelectedDatasetData: PropTypes.func,
  setCompareFromOutside: PropTypes.func,
};
