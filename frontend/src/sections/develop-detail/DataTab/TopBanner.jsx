import React, { useEffect, useState } from "react";
import { Alert, Button } from "@mui/material";
import { useParams } from "react-router";
import UploadProgressNotification from "./DevelopDataNotificationBar";
import { ShowComponent } from "src/components/show";
import { useQuery } from "@tanstack/react-query";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useWorkspacesList } from "src/api/workspaces/list";

/**
 * Where a dataset of another workspace is: `This dataset moved to Billing
 * Agents` after a move, `This dataset is in Billing Agents` otherwise; null
 * when it is in the current workspace (or unknown).
 */
export function otherWorkspaceNotice({
  datasetId,
  currentWorkspaceId,
  names,
  workspaces,
}) {
  if (!datasetId || !currentWorkspaceId) return null;
  const item = (names ?? []).find(
    (d) => (d.dataset_id ?? d.datasetId) === datasetId,
  );
  const ws = item?.workspace_id;
  if (!ws || ws === currentWorkspaceId) return null;
  const row = (workspaces ?? []).find((w) => w.id === ws);
  const name = row?.display_name || row?.name;
  if (!name) return null;
  const moved = Boolean(item.moved_from_workspace_id);
  return {
    workspaceId: ws,
    workspaceName: name,
    moved,
    text: moved
      ? `This dataset moved to ${name}`
      : `This dataset is in ${name}`,
  };
}

/**
 * W3 integration (W3-11): a link to a dataset that lives in another workspace
 * (moved there, or made there) says where it is and offers the switch
 * (the app's own switchWorkspace). The dataset's workspace comes from the
 * name picker's workspace_id (all_workspaces=true).
 */
export const OtherWorkspaceBanner = () => {
  const { dataset } = useParams();
  const { currentWorkspaceId, switchWorkspace } = useWorkspace();
  const { data: names } = useDevelopDatasetList(
    undefined,
    undefined,
    { enabled: Boolean(dataset && currentWorkspaceId) },
    { all_workspaces: true },
  );
  const { data: workspaces } = useWorkspacesList({
    enabled: Boolean(dataset && currentWorkspaceId),
  });
  const [busy, setBusy] = useState(false);
  const notice = otherWorkspaceNotice({
    datasetId: dataset,
    currentWorkspaceId,
    names,
    workspaces,
  });
  if (!notice) return null;
  return (
    <Alert
      severity="warning"
      sx={{ mb: 1, alignItems: "center" }}
      action={
        <Button
          color="inherit"
          size="small"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            switchWorkspace(notice.workspaceId, currentWorkspaceId).catch(() =>
              setBusy(false),
            );
          }}
        >
          Switch workspace
        </Button>
      }
    >
      {notice.text}
    </Alert>
  );
};

const TopBanner = () => {
  const [notificationData, setNotificationData] = useState([]);
  const [showProgressBar, setShowProgressBar] = useState(false);

  const { data } = useQuery({
    queryKey: ["develop-data"],
    enabled: false,
  });

  useEffect(() => {
    const metaData = data?.result?.metadata;
    setNotificationData(metaData);
    setShowProgressBar(!data?.result?.datasetConfig?.dismissBanner);
  }, [data]);

  return (
    <>
      <OtherWorkspaceBanner />
      <ShowComponent
        condition={
          showProgressBar && notificationData && notificationData?.status
        }
      >
        <UploadProgressNotification
          showReason={
            notificationData?.status?.datasetStatus == "PartialDataExtracted"
          }
          status={notificationData?.status}
          onClose={() => setShowProgressBar(false)}
        />
      </ShowComponent>
    </>
  );
};

export default TopBanner;
