import {
  Box,
  Radio,
  RadioGroup,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import Section from "src/components/section";
import React, { useMemo } from "react";
import { StyledBox } from "./CreateScenariosForm";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { useReplaySessionsStoreShallow } from "./store";
import PropTypes from "prop-types";
import { useGetScenarioList } from "../../../../api/scenarios/scenarios";
import { useScrollEnd } from "../../../../hooks/use-scroll-end";
import { useDebounce } from "../../../../hooks/use-debounce";
import SvgColor from "../../../../components/svg-color/svg-color";
import { SCENARIO_TYPES } from "../../../../pages/dashboard/scenarios/common";
const ScenarioGroupCard = ({ group, selectedGroup, setSelectedGroup }) => {
  return (
    <Section
      variant="panel"
      sx={{ p: (theme) => theme.spacing(1, 1.5), mb: 1, cursor: "pointer" }}
      onClick={() => setSelectedGroup(group.id)}
    >
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Radio
          checked={selectedGroup === group.id}
          onChange={() => setSelectedGroup(group.id)}
          value={group.id}
          sx={{ p: 0 }}
        />
        <Typography
          typography={"s1"}
          fontWeight={"fontWeightRegular"}
          color={"text.primary"}
        >
          {group.name}
        </Typography>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{
            ml: "auto",
            bgcolor: "background.neutral",
            padding: (theme) => theme.spacing(0.5, 1.5),
            borderRadius: (theme) => theme.spacing(0.25),
            minWidth: "110px",
            textAlign: "center",
          }}
        >
          <SvgColor
            sx={{
              width: "16px",
              height: "16px",
              bgcolor: "text.primary",
            }}
            src={SCENARIO_TYPES[group.scenarioType].icon}
          />
          <Typography
            typography={"s3"}
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            Scenarios :{group?.datasetRows ?? 0}
          </Typography>
        </Stack>
      </Stack>
    </Section>
  );
};

ScenarioGroupCard.propTypes = {
  group: PropTypes.object.isRequired,
  selectedGroup: PropTypes.string,
  setSelectedGroup: PropTypes.func.isRequired,
};

const ScenarioGroupsSkeleton = () => {
  return (
    <Stack spacing={1.5}>
      {[...Array(6)].map((_, index) => (
        <Skeleton
          key={index}
          variant="rectangular"
          height={56}
          sx={{ borderRadius: 1 }}
        />
      ))}
    </Stack>
  );
};

const ScenarioGroups = ({ selectedGroup, setSelectedGroup, searchQuery }) => {
  const debouncedSearchQuery = useDebounce(searchQuery, 500);
  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } =
    useGetScenarioList(debouncedSearchQuery);
  const scrollContainer = useScrollEnd(() => {
    if (!hasNextPage || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, hasNextPage]);

  const scenarios = useMemo(
    () => data?.pages?.flatMap((page) => page.data.results) || [],
    [data],
  );

  if (isLoading) {
    return <ScenarioGroupsSkeleton />;
  }

  return (
    <Box
      ref={scrollContainer}
      sx={{
        p: 1.5,
        bgcolor: "background.neutral",
        height: "calc(100vh - 430px)",
        overflowY: "auto",
      }}
    >
      <RadioGroup
        value={selectedGroup}
        onChange={(e) => setSelectedGroup(e.target.value)}
      >
        <Stack spacing={0}>
          {scenarios?.map((group) => (
            <ScenarioGroupCard
              key={group.id}
              group={group}
              selectedGroup={selectedGroup}
              setSelectedGroup={setSelectedGroup}
            />
          ))}
          {isFetchingNextPage && <ScenarioGroupsSkeleton />}
        </Stack>
      </RadioGroup>
    </Box>
  );
};

ScenarioGroups.propTypes = {
  selectedGroup: PropTypes.string,
  setSelectedGroup: PropTypes.func.isRequired,
  searchQuery: PropTypes.string,
};

const AddToScenarioGroups = () => {
  const { searchQuery, setSearchQuery, selectedGroup, setSelectedGroup } =
    useReplaySessionsStoreShallow((s) => ({
      searchQuery: s.searchQuery,
      setSearchQuery: s.setSearchQuery,
      selectedGroup: s.selectedGroup,
      setSelectedGroup: s.setSelectedGroup,
    }));
  return (
    <Stack gap={2}>
      <StyledBox
        sx={{
          display: "flex",
          flexDirection: "row",
          gap: 10,
          alignItems: "center",
        }}
      >
        <Stack gap={0}>
          <Typography
            typography={"s2_1"}
            color={"text.disabled"}
            fontWeight={"fontWeightMedium"}
          >
            Scenario Name
          </Typography>
          <Typography
            typography={"s1_2"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            Customer Agent Support
          </Typography>
        </Stack>
        <Stack gap={0}>
          <Typography
            typography={"s2_1"}
            color={"text.disabled"}
            fontWeight={"fontWeightMedium"}
          >
            No. of scenarios
          </Typography>
          <Typography
            typography={"s1_2"}
            color={"text.primary"}
            fontWeight={"fontWeightMedium"}
          >
            2
          </Typography>
        </Stack>
      </StyledBox>
      <Stack gap={0}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          Add to scenario group
        </Typography>
        <Typography
          typography={"s2_1"}
          color={"text.primary"}
          fontWeight={"fontWeightRegular"}
        >
          Choose scenario groups to move your new scenarios to
        </Typography>
      </Stack>
      <FormSearchField
        size="small"
        placeholder="Search"
        sx={{ minWidth: "360px" }}
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        autoComplete="off"
      />
      <ScenarioGroups
        selectedGroup={selectedGroup}
        setSelectedGroup={setSelectedGroup}
        searchQuery={searchQuery}
      />
    </Stack>
  );
};
AddToScenarioGroups.displayName = "AddToScenarioGroups";

export default React.memo(AddToScenarioGroups);
