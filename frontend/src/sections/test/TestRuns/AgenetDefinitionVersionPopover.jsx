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
import { useScrollEnd } from "../../../hooks/use-scroll-end";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useSelectedAgentDefinitionStore } from "./states";
import { ShowComponent } from "../../../components/show";
import { useUpdateTestRuns } from "src/api/tests/testRuns";
import { useParams } from "react-router-dom";
import { debounce } from "lodash";
import { useDebounce } from "src/hooks/use-debounce";
import { useAgentDefinitionVersions } from "src/api/agent-definition/agent-definition-version";
import { ComponentApiMapping } from "./common";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "src/components/snackbar";
import { useQueryClient } from "@tanstack/react-query";

const AgentDefinitionSkeletonItem = () => (
  <Box sx={{ display: "flex", gap: 1, padding: 1, alignItems: "center" }}>
    <Skeleton variant="circular" width={20} height={15} />
    <Skeleton variant="rounded" width="100%" height={15} />
  </Box>
);

const AgentDefinitionVersionPopoverChild = ({ selectedAgent }) => {
  const [search, setSearch] = useState("");
  const theme = useTheme();
  const { testId } = useParams();
  const debouncedSearch = useDebounce(search, 300);
  const queryClient = useQueryClient();
  const pastAgentDefinitionVersion = useRef(null);
  const [confirmDisableToolEvaluation, setConfirmDisableToolEvaluation] =
    useState(false);

  const {
    data: agentDefVersions,
    fetchNextPage: fetchNextAgentVersions,
    isFetchingNextPage: isFetchingAgentVersionsNextPage,
    isPending,
  } = useAgentDefinitionVersions({
    selectedAgentId: selectedAgent?.id,
  });

  const versionOptions = useMemo(() => {
    return agentDefVersions?.pages?.reduce((acc, curr) => {
      const newOptions = curr?.results
        ?.map((result) => ({
          label: result?.version_name_display,
          value: result?.id,
        }))
        ?.filter(
          (item) =>
            item.label &&
            item.label.toLowerCase().includes(debouncedSearch.toLowerCase()),
        );

      return [...acc, ...newOptions];
    }, []);
  }, [agentDefVersions, debouncedSearch]);

  const { selectedAgentDefinitionVersion, setSelectedAgentDefinitionVersion } =
    useSelectedAgentDefinitionStore();

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingAgentVersionsNextPage) return;
    fetchNextAgentVersions();
  }, [fetchNextAgentVersions, isFetchingAgentVersionsNextPage, isPending]);

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
        onSuccess: () => {
          setConfirmDisableToolEvaluation(false);
          pastAgentDefinitionVersion.current = null;
          queryClient.invalidateQueries({
            queryKey: ["test-runs-detail", testId],
          });
        },
      });
    }, 300),
  ).current;

  return (
    <Box>
      <Box>
        <FormSearchField
          placeholder="Search Version..."
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
          maxWidth: 400,
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
          {versionOptions?.map((option) => {
            const { value: id, label: agentName } = option;
            const labelId = `checkbox-list-label-${id}`;
            const isSelected = selectedAgentDefinitionVersion?.value === id;

            return (
              <>
                <ListItem key={id} disablePadding>
                  <ListItemButton
                    role={undefined}
                    onClick={() => {
                      pastAgentDefinitionVersion.current =
                        selectedAgentDefinitionVersion;
                      setSelectedAgentDefinitionVersion(option);
                      debouncedUpdateTestRuns({
                        version: id,
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
                    <ListItemText
                      id={labelId}
                      primary={agentName}
                      primaryTypographyProps={{
                        typography: "s2",
                        fontWeight: "medium",
                      }}
                      secondaryTypographyProps={{ typography: "s2" }}
                    />
                  </ListItemButton>
                </ListItem>
              </>
            );
          })}
          <ShowComponent condition={isFetchingAgentVersionsNextPage}>
            <>
              <AgentDefinitionSkeletonItem />
              <AgentDefinitionSkeletonItem />
              <AgentDefinitionSkeletonItem />
            </>
          </ShowComponent>
        </ShowComponent>
      </List>
      <ConfirmDialog
        title="Confirm Disable Tool Evaluation"
        content="You have tool evaluation enabled in this test run, but you are changing to a agent definition version which doesn't have api key or assistant id."
        action={
          <LoadingButton
            variant="contained"
            color="error"
            onClick={() => {
              debouncedUpdateTestRuns({
                version: selectedAgentDefinitionVersion?.value,
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
          setSelectedAgentDefinitionVersion(pastAgentDefinitionVersion.current);
          pastAgentDefinitionVersion.current = null;
        }}
      />
    </Box>
  );
};

AgentDefinitionVersionPopoverChild.propTypes = {
  selectedAgent: PropTypes.object,
};

const AgentDefinitionVersionPopover = ({
  open,
  onClose,
  anchor,
  selectedAgent,
}) => {
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
      <AgentDefinitionVersionPopoverChild selectedAgent={selectedAgent} />
    </Popover>
  );
};

AgentDefinitionVersionPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchor: PropTypes.object,
  selectedAgent: PropTypes.object,
};

export default AgentDefinitionVersionPopover;
