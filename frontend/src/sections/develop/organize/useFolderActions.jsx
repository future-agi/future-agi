// The folder rail's ⋯ actions on the dataset list (W9-SCOPE §5 A6, M4
// mount): Rename folder…, Move folder to…, Remove folder. The dialog plans
// over every dataset in the folder's subtree, read from the server
// (`folder is under <path>`, every page, whatever the list's search or
// page), never the rows the list happens to show.
import React, { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { notify } from "src/sections/common/ux/toast";
import { errorSentence } from "src/api/develop/dataset-org";
import { datasetQueryParams } from "../hooks/useDatasetListQuery";
import FolderActionDialog from "./FolderActionDialog";
import RealFolderActionDialog from "./RealFolderActionDialog";
import { datasetCaps } from "../realBackendCaps";
import { folderCondition } from "./folderRailModel";
import { urlFromFilters } from "./datasetListView";
import { showOrganizeToast } from "./useDatasetOrganize";

/** The query's page cap (get-datasets' MAX_PAGE_SIZE). */
const PAGE_SIZE = 100;

/**
 * Every dataset under `path` ({id, name, folder}), all pages. Pure apart
 * from `get`.
 */
export async function fetchFolderSubtree(path, get = axios.get) {
  const out = [];
  for (let page = 0; ; page += 1) {
    // eslint-disable-next-line no-await-in-loop
    const { data } = await get(endpoints.develop.w9.datasetsQuery, {
      params: datasetQueryParams({
        page,
        pageSize: PAGE_SIZE,
        filters: urlFromFilters([folderCondition(path)]),
        starredFirst: false,
      }),
    });
    const result = data?.result ?? {};
    (result.datasets ?? []).forEach((d) =>
      out.push({ id: d.id, name: d.name, folder: d.folder ?? "" }),
    );
    if (page + 1 >= (result.total_pages ?? 0)) break;
  }
  return out;
}

/** `const folders = useFolderActions(); folders.open("rename", path); {folders.dialog}` */
export default function useFolderActions(folders = [], onChanged) {
  const real = datasetCaps().real;
  const [target, setTarget] = useState(null); // {action, path}
  // The subtree as read when the dialog opened: the dialog keeps it while
  // it saves (its own writes refresh the lists, not this plan).
  const [plan, setPlan] = useState(null); // {action, path, datasets}
  const subtree = useQuery({
    queryKey: ["datasets", "query", "folder-subtree", target?.path ?? ""],
    queryFn: () => fetchFolderSubtree(target.path),
    enabled: !real && Boolean(target) && !plan,
    // Read fresh every time a dialog opens: the plan must see every move.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
  useEffect(() => {
    if (real || !target || plan) return;
    if (subtree.isError) {
      notify(errorSentence(subtree.error), {
        variant: "error",
        key: "folder-action",
      });
      setTarget(null);
      return;
    }
    if (subtree.isSuccess && !subtree.isFetching)
      setPlan({ ...target, datasets: subtree.data });
  }, [
    real,
    target,
    plan,
    subtree.isError,
    subtree.error,
    subtree.isSuccess,
    subtree.isFetching,
    subtree.data,
  ]);
  const open = useCallback(
    (action, path) => {
      const folder = folders.find((candidate) => candidate.path === path);
      if (real && action !== "create" && !folder) return;
      setPlan(null);
      setTarget({ action, path, folder });
    },
    [real, folders],
  );
  const close = useCallback(() => {
    setTarget(null);
    setPlan(null);
  }, []);
  return {
    open,
    dialog:
      real && target ? (
        <RealFolderActionDialog
          key={`${target.action}:${target.path}`}
          action={target.action}
          folder={target.folder}
          onClose={close}
          onDone={(toast) => {
            onChanged?.(toast.folderChange);
            showOrganizeToast(toast);
          }}
        />
      ) : plan ? (
        <FolderActionDialog
          open
          action={plan.action}
          path={plan.path}
          datasets={plan.datasets}
          onClose={close}
          onDone={showOrganizeToast}
        />
      ) : null,
  };
}
