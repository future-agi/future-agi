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

const LIMIT_CHECKBOX_MESSAGE =
  "Compare limit is upto 3 version only, Deselect other options to select this one";

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

  const { selectedVersions, applyCompare } = usePromptWorkbenchContext();

  const [isCompareActive, setIsCompareActive] = useState(false);

  const [compareSelectedVersions, setCompareSelectedVersions] = useState([]);

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
      <Box sx={{ paddingX: 2, paddingTop: 2 }}>
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
          sx={{ position: "absolute", top: "10px", right: "12px" }}
        >
          <Iconify icon="mingcute:close-line" color="text.primary" />
        </IconButton>
      </Box>

      <Divider sx={{ borderColor: "divider" }} />
      <Box
        sx={{
          paddingX: 2,
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
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
              sx={{ height: "30px" }}
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
            const isChecked =
              selectedVersions?.[0]?.version === version.template_version ||
              compareSelectedVersions.find(
                (v) => v.template_version === version.template_version,
              );

            const isDisabled =
              selectedVersions?.[0]?.version === version.template_version ||
              (compareSelectedVersions.length === 2 && !isChecked);

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
        condition={compareSelectedVersions.length > 0 && isCompareActive}
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

  return (
    <Drawer
      anchor="right"
      open={versionHistoryOpen}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "1028px",
          borderRadius: "10px",
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
