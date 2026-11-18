import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  Radio,
  useTheme,
  Skeleton,
} from "@mui/material";
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useScrollEnd } from "../../../hooks/use-scroll-end";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useSelectedAgentDefinitionStore } from "./states";
import { ShowComponent } from "../../../components/show";
import { useUpdateTestRuns } from "src/api/tests/testRuns";
import { useParams } from "react-router-dom";
import { debounce } from "lodash";
import { ComponentApiMapping } from "./common";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import CustomTooltip from "src/components/tooltip";

const AgentDefinitionSkeletonItem = () => (
  <Box sx={{ display: "flex", gap: 1, padding: 1, alignItems: "center" }}>
    <Skeleton variant="circular" width={20} height={15} />
    <Skeleton variant="rounded" width="100%" height={15} />
  </Box>
);

const AgentDefinitionPopoverChild = ({ simulationType }) => {
  const [search, setSearch] = useState("");
  const theme = useTheme();
  const { testId } = useParams();
  const pastAgentDefinitionId = useRef(null);
  const [confirmDisableToolEvaluation, setConfirmDisableToolEvaluation] =
    useState(false);
  const queryClient = useQueryClient();

  const { data, isFetchingNextPage, fetchNextPage, isPending } =
    useInfiniteQuery({
      queryFn: ({ pageParam }) =>
        axios.get(endpoints.agentDefinitions.list, {
          params: {
            page: pageParam,
            limit: 10,
            search,
            ...(simulationType && { agent_type: simulationType }),
          },
        }),
      queryKey: ["agent-definition-list", search, simulationType],
      getNextPageParam: ({ data }) =>
        data?.next ? data?.current_page + 1 : null,
      initialPageParam: 1,
    });

  const agentDefinitions = useMemo(
    () => data?.pages.flatMap((page) => page.data.results),
    [data],
  );

  const {
    selectedAgentDefinition,
    setSelectedAgentDefinition,
    setSelectedAgentDefinitionVersion,
  } = useSelectedAgentDefinitionStore();

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  const { mutate: updateTestRuns, isPending: isUpdatingTestRuns } =
    useUpdateTestRuns(testId, {
      meta: { errorHandled: true },
      onError: (error) => {
        if (
          error?.result?.errorCode === ComponentApiMapping.ToolEvaluationApiKey
        ) {
          setConfirmDisableToolEvaluation(true);
        } else {
          enqueueSnackbar(`${error?.result}`, {
            variant: "error",
          });
        }
      },
    });

  const debouncedUpdateTestRuns = React.useRef(
    debounce((data) => {
      updateTestRuns(data, {
        onSuccess: (data) => {
          // set version here
          const version = data?.data?.agent_version ?? data?.data?.agentVersion;
          setSelectedAgentDefinitionVersion({
            value: version?.id,
            label: version?.name,
          });
          setConfirmDisableToolEvaluation(false);
          pastAgentDefinitionId.current = null;
          queryClient.invalidateQueries({
            queryKey: ["test-runs-detail", testId],
          });
        },
      });
    }, 300),
  ).current;

  return (
    <>
      <Box>
        <Box>
          <FormSearchField
            placeholder="Search Agents..."
            size="small"
            searchQuery={search}
            onChange={(e) => setSearch(e.target.value)}
            fullWidth
            autoFocus
            sx={{
              margin: theme.spacing(1),
              width: `calc(100% - ${theme.spacing(2)})`,
            }}
            InputProps={{}}
          />
        </Box>
        <List
          sx={{
            width: "100%",

            bgcolor: "background.paper",
            maxHeight: 200,
            paddingX: 0.5,
            overflowY: "auto",
          }}
          dense
          ref={scrollContainer}
        >
          <ShowComponent condition={isPending}>
            <AgentDefinitionSkeletonItem />
            <AgentDefinitionSkeletonItem />
            <AgentDefinitionSkeletonItem />
          </ShowComponent>
          <ShowComponent condition={!isPending}>
            {agentDefinitions?.map((agent) => {
              const { id, agent_name: agentName } = agent;
              const labelId = `checkbox-list-label-${id}`;
              const isSelected = selectedAgentDefinition?.id === id;

              return (
                <>
                  <ListItem key={id} disablePadding>
                    <ListItemButton
                      role={undefined}
                      onClick={() => {
                        pastAgentDefinitionId.current = selectedAgentDefinition;
                        setSelectedAgentDefinition(agent);
                        debouncedUpdateTestRuns({
                          agent_definition_id: agent?.id,
                        });
                      }}
                      dense
                    >
                      <ListItemIcon>
                        <Radio
                          edge="start"
                          tabIndex={-1}
                          disableRipple
                          inputProps={{ "aria-labelledby": labelId }}
                          sx={{ padding: 0 }}
                          checked={isSelected}
                        />
                      </ListItemIcon>
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          width: "100%",
                        }}
                      >
                        <CustomTooltip
                          show={true}
                          size="small"
                          title={agentName}
                          arrow={true}
                        >
                          <ListItemText
                            id={labelId}
                            primary={
                              agentName?.length > 30
                                ? agentName.slice(0, 30) + "..."
                                : agentName
                            }
                            primaryTypographyProps={{
                              typography: "s2",
                              fontWeight: "medium",
                            }}
                            secondaryTypographyProps={{ typography: "s2" }}
                          />
                        </CustomTooltip>
                      </Box>
                    </ListItemButton>
                  </ListItem>
                </>
              );
            })}
            <ShowComponent condition={isFetchingNextPage}>
              <>
                <AgentDefinitionSkeletonItem />
                <AgentDefinitionSkeletonItem />
                <AgentDefinitionSkeletonItem />
              </>
            </ShowComponent>
          </ShowComponent>
        </List>
      </Box>
      <ConfirmDialog
        title="Confirm Disable Tool Evaluation"
        content="You have tool evaluation enabled in this test run, but you are changing to a agent definition which doesn't have api key or assistant id."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            onClick={() => {
              debouncedUpdateTestRuns({
                agent_definition_id: selectedAgentDefinition?.id,
                enable_tool_evaluation: false,
              });
            }}
            size="small"
            sx={{ lineHeight: 1 }}
            loading={isUpdatingTestRuns}
          >
            Disable Tool Evaluation
          </LoadingButton>
        }
        open={confirmDisableToolEvaluation}
        onClose={() => {
          setConfirmDisableToolEvaluation(false);
          setSelectedAgentDefinition(pastAgentDefinitionId.current);
          pastAgentDefinitionId.current = null;
        }}
      />
    </>
  );
};

AgentDefinitionPopoverChild.propTypes = {
  simulationType: PropTypes.string,
};

const AgentDefinitionPopover = ({ open, onClose, anchor, simulationType }) => {
  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorEl={anchor}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "right",
      }}
      transformOrigin={{
        vertical: "top",
        horizontal: "right",
      }}
      PaperProps={{
        sx: {
          minWidth: anchor?.clientWidth,
        },
      }}
    >
      <AgentDefinitionPopoverChild simulationType={simulationType} />
    </Popover>
  );
};

AgentDefinitionPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchor: PropTypes.object,
  simulationType: PropTypes.string,
};

export default AgentDefinitionPopover;
