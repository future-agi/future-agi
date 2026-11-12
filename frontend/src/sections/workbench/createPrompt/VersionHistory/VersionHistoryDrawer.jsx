import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Typography,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import axios, { endpoints } from "src/utils/axios";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { ShowComponent } from "src/components/show";

import Iconify from "../../../../components/iconify";
import { usePromptWorkbenchContext } from "../WorkbenchContext";

import EmptyVersions from "./EmptyVersions";
import VersionCard from "./VersionCard";
import VersionCardSkeleton from "./VersionCardSkeleton";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import CustomAgentTabs from "src/sections/agents/CustomAgentTabs";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const LIMIT_CHECKBOX_MESSAGE =
  "You can compare up to 3 versions. Deselect one to select this version.";

const usePromptVersions = (id, activeTab) => {
  const sendCommit = activeTab === "commit_history";

  const _queryParams = useInfiniteQuery({
    queryKey: ["prompt-versions", id, sendCommit],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.develop.runPrompt.getPromptVersions(), {
        params: {
          template_id: id,
          page: pageParam,
          ...(sendCommit && { is_commit: sendCommit }),
        },
      }),
    getNextPageParam: (o) => {
      const nextPage = o.data.next ? o.data.current_page + 1 : null;
      return nextPage;
    },
    initialPageParam: 1,
  });

  const versions = useMemo(
    () =>
      _queryParams.data?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.results],
        [],
      ) || [],
    [_queryParams.data],
  );

  return { versions, ..._queryParams };
};
const tabs = [
  { label: "History", value: "history" },
  { label: "Commit History", value: "commit_history" },
];

const VersionHistoryChild = ({ onClose }) => {
  const { id } = useParams();
  const [activeTab, setActiveTab] = useState("history");
  const { versions, fetchNextPage, isPending, isFetchingNextPage } =
    usePromptVersions(id, activeTab);

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) {
      return;
    }
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  const { selectedVersions, applyCompare, isEmptyPrompt } =
    usePromptWorkbenchContext();

  const [isCompareActive, setIsCompareActive] = useState(false);

  const [compareSelectedVersions, setCompareSelectedVersions] = useState([]);

  // The open version is the first pane of a comparison (ticked and locked
  // here), unless it is an empty draft: then it never joins on its own, and
  // up to 3 versions are picked here, at least 2 to compare.
  const openIsEmptyDraft = Boolean(
    selectedVersions?.[0]?.isDraft && isEmptyPrompt?.includes(0),
  );
  const openVersion = openIsEmptyDraft ? null : selectedVersions?.[0]?.version;
  const maxPicks = openIsEmptyDraft ? 3 : 2;
  const minPicks = openIsEmptyDraft ? 2 : 1;

  const { role: userRole } = useAuthContext();

  return (
    <Box
      sx={{
        display: "flex",
        gap: 2,
        flexDirection: "column",
        overflow: "hidden",
        height: "100%",
      }}
    >
      {/* Room on the right for the close button, so the description never
          runs under it on a narrow screen. */}
      <Box sx={{ paddingLeft: 2, paddingRight: 7, paddingTop: 2 }}>
        <Typography variant="m3" fontWeight={"fontWeightSemiBold"}>
          History
        </Typography>
        <Typography
          typography="s1"
          fontWeight={"fontWeightRegular"}
          color="text.secondary"
        >
          Access the history of prompts along with variables, tags, and model
          versions.
        </Typography>
        <IconButton
          onClick={onClose}
          aria-label="Close history"
          sx={{ position: "absolute", top: "10px", right: "12px" }}
        >
          <Iconify icon="solar:close-linear" color="text.primary" />
        </IconButton>
      </Box>

      <Divider sx={{ borderColor: "divider" }} />
      {/* When the tabs and Select to compare do not fit on
          one row (a phone), the button moves under the tabs instead of
          running past the right edge. */}
      <Box
        sx={{
          paddingX: 2,
          display: "flex",
          flexDirection: "row",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          rowGap: 1,
        }}
      >
        <CustomAgentTabs
          value={activeTab}
          onChange={(_, newValue) => {
            setCompareSelectedVersions([]);
            setIsCompareActive(false);
            setActiveTab(newValue);
          }}
          tabs={tabs}
        />
        <ShowComponent
          condition={RolePermission.PROMPTS[PERMISSIONS.UPDATE][userRole]}
        >
          <Box paddingX={2}>
            <Button
              size="small"
              variant="outlined"
              color="primary"
              onClick={() => {
                setIsCompareActive(!isCompareActive);
                setCompareSelectedVersions([]);
              }}
            >
              {isCompareActive ? "Cancel select" : "Select to compare"}
            </Button>
          </Box>
        </ShowComponent>
      </Box>

      <ShowComponent condition={!isPending && versions.length === 0}>
        <EmptyVersions activeTab={activeTab} />
      </ShowComponent>
      <ShowComponent condition={isPending}>
        <Box
          sx={{
            flex: 1,
            gap: 2,
            paddingX: 2,
            flexDirection: "column",
            display: "flex",
            overflowY: "auto",
          }}
        >
          {Array.from({ length: 7 }).map((_, index) => (
            <VersionCardSkeleton key={index} />
          ))}
        </Box>
      </ShowComponent>
      <ShowComponent condition={versions.length > 0}>
        <Box
          sx={{
            flex: 1,
            gap: 2,
            flexDirection: "column",
            display: "flex",
            overflowY: "auto",
            paddingX: 2,
          }}
          ref={scrollContainer}
        >
          {versions?.map((version, _) => {
            const isOpen =
              openVersion != null && openVersion === version.template_version;
            const isChecked =
              isOpen ||
              compareSelectedVersions.find(
                (v) => v.template_version === version.template_version,
              );

            const isDisabled =
              isOpen ||
              (compareSelectedVersions.length >= maxPicks && !isChecked);

            const checkboxMessage = !isChecked ? LIMIT_CHECKBOX_MESSAGE : "";

            return (
              <VersionCard
                key={version.id}
                version={version}
                showCheckbox={isCompareActive}
                disableCheckbox={isDisabled}
                checked={isChecked}
                setChecked={(e) => {
                  if (e.target.checked) {
                    setCompareSelectedVersions((pre) => [...pre, version]);
                  } else {
                    setCompareSelectedVersions(
                      compareSelectedVersions.filter(
                        (v) => v.id !== version.id,
                      ),
                    );
                  }
                }}
                checkboxMessage={checkboxMessage}
              />
            );
          })}
          <ShowComponent condition={isFetchingNextPage}>
            {Array.from({ length: 3 }).map((_, index) => (
              <VersionCardSkeleton key={index} />
            ))}
          </ShowComponent>
        </Box>
      </ShowComponent>
      <ShowComponent
        condition={
          compareSelectedVersions.length >= minPicks && isCompareActive
        }
      >
        <Box sx={{ display: "flex", justifyContent: "flex-end", padding: 2 }}>
          <Button
            variant="contained"
            color="primary"
            sx={{ width: "200px" }}
            onClick={() => {
              applyCompare(compareSelectedVersions);
              onClose();
              trackEvent(Events.promptCompareClicked, {
                [PropertyName.promptId]: id,
                [PropertyName.type]: "version history",
              });
            }}
          >
            Compare
          </Button>
        </Box>
      </ShowComponent>
    </Box>
  );
};

VersionHistoryChild.propTypes = {
  onClose: PropTypes.func,
};

const VersionHistoryDrawer = () => {
  const { versionHistoryOpen, setVersionHistoryOpen } =
    usePromptWorkbenchContext();

  const onClose = () => setVersionHistoryOpen(false);

  const covered = useDrawerCovered(versionHistoryOpen);
  return (
    <Drawer
      anchor="right"
      open={versionHistoryOpen}
      sx={coveredDrawerSx(covered)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "960px",
          // Never wider than the screen (it was 960 px on
          // any screen, so below 960 px its left side was cut off).
          maxWidth: "100vw",
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <VersionHistoryChild onClose={onClose} />
    </Drawer>
  );
};

VersionHistoryDrawer.propTypes = {};

export default VersionHistoryDrawer;
