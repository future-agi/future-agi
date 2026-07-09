import {
  Box,
  Checkbox,
  debounce,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Popover,
  Skeleton,
  useTheme,
} from "@mui/material";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import { useScrollEnd } from "../../../hooks/use-scroll-end";
import { useSelectedScenariosStore } from "./states";
import { ShowComponent } from "../../../components/show";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useUpdateTestRuns } from "src/api/tests/testRuns";
import { useGetScenarioList } from "src/api/scenarios/scenarios";
import { AGENT_TYPES } from "src/sections/agents/constants";

const ScenarioSkeletonItem = () => (
  <Box sx={{ display: "flex", gap: 1, padding: 1, alignItems: "center" }}>
    <Skeleton variant="rounded" width={20} height={15} />
    <Skeleton variant="rounded" width="100%" height={15} />
  </Box>
);

const ScenarioPopoverChild = ({ simulationType, testId: testIdProp }) => {
  const { testId: testIdFromParams } = useParams();
  const testId = testIdProp || testIdFromParams;
  const [search, setSearch] = useState("");
  const theme = useTheme();

  const { mutate: updateTestRuns } = useUpdateTestRuns(testId);

  const debouncedUpdateTestRuns = React.useRef(
    debounce((data) => {
      if (testId) {
        updateTestRuns(data);
      }
    }, 300),
  ).current;

  const { data, isFetchingNextPage, fetchNextPage, isPending } =
    useGetScenarioList(search, {
      simulationType,
      staleTime: Infinity,
      params: {
        agent_type: AGENT_TYPES.CHAT,
      },
    });

  const scenarios = useMemo(
    () => data?.pages.flatMap((page) => page.data.results),
    [data],
  );

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  const { selectedScenarios, setSelectedScenarios } =
    useSelectedScenariosStore();

  const handleToggle = (value) => {
    const newSelectedScenarios = selectedScenarios?.includes(value)
      ? selectedScenarios?.filter((id) => id !== value)
      : [...selectedScenarios, value];
    setSelectedScenarios(newSelectedScenarios);
    debouncedUpdateTestRuns({
      scenarios: newSelectedScenarios,
    });
  };

  return (
    <Box>
      <Box>
        <FormSearchField
          placeholder="Search Scenarios..."
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
        ref={scrollContainer}
        dense
      >
        <ShowComponent condition={isPending}>
          <ScenarioSkeletonItem />
          <ScenarioSkeletonItem />
        </ShowComponent>
        <ShowComponent condition={!isPending}>
          {scenarios?.map(({ id, name, dataset_rows:datasetRows }) => {
            const labelId = `checkbox-list-label-${id}`;
            const isSelected = selectedScenarios?.includes(id);

            return (
              <ListItem key={id} disablePadding>
                <ListItemButton
                  role={undefined}
                  onClick={() => handleToggle(id)}
                  dense
                >
                  <ListItemIcon>
                    <Checkbox
                      edge="start"
                      checked={isSelected}
                      tabIndex={-1}
                      disableRipple
                      inputProps={{ "aria-labelledby": labelId }}
                      sx={{ padding: 0 }}
                    />
                  </ListItemIcon>
                  <ListItemText
                    id={labelId}
                    primary={name}
                    secondary={`${datasetRows} records`}
                    primaryTypographyProps={{
                      typography: "s2",
                      fontWeight: "medium",
                    }}
                    secondaryTypographyProps={{ typography: "s2" }}
                  />
                </ListItemButton>
              </ListItem>
            );
          })}
        </ShowComponent>
        <ShowComponent condition={isFetchingNextPage}>
          <ScenarioSkeletonItem />
          <ScenarioSkeletonItem />
          <ScenarioSkeletonItem />
        </ShowComponent>
      </List>
    </Box>
  );
};
ScenarioPopoverChild.propTypes = {
  simulationType: PropTypes.string,
  testId: PropTypes.string,
};

const ScenarioPopover = ({ open, onClose, anchor, simulationType, testId }) => {
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
      <ScenarioPopoverChild simulationType={simulationType} testId={testId} />
    </Popover>
  );
};

ScenarioPopover.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  anchor: PropTypes.object,
  simulationType: PropTypes.string,
  testId: PropTypes.string,
};

export default ScenarioPopover;
