import {
  Box,
  Button,
  useTheme,
  Alert,
  CircularProgress,
  Link,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import {
  getDatasetQueryOptions,
  useDevelopDatasetList,
  useScenariosList,
} from "src/api/develop/develop-detail";
import DatasetSelectDropDown from "./DatasetSelectDropDown";
import CompareDatasetDrawer from "./CompareDatasetDrawer";
import BaseColumnDrawer from "./BaseColumnsDrawer";
import CompareDatasetsTop from "./CompareDatasetsTop";
import DropdownWithSearch from "../common/DropdownWithSearch";
import { useSearchParams } from "src/routes/hooks";
import BackButton from "./Common/BackButton";
import SvgColor from "src/components/svg-color";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import CustomTooltip from "src/components/tooltip";
import Share from "src/components/Share/Share";
import ConfigureDatasetModal from "./Common/ConfigureDatasetModal";
import {
  useDatasetOriginStore,
  useProcessingStore,
  useRunExperimentStoreShallow,
  useRunOptimizationStore,
} from "./states";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

const DatasetsSelectRow = ({
  setIsChooseWinnerSelected,
  setCurrentTab,
  currentTab,
  baseColumn,
  setIsCompareDataset,
  setBaseColumnInParent,
  setSelectedDatasetsValuesInParent,
  setSelectedDatasetData,
  isCompareDataset,
  selectedDatasetData,
  compareFromOutside,
  setCompareFromOutSide,
  setIsCommonColumn,
  setIsChooseWinnerButtonVisible,
  shouldHideDevelopBar,
  hideScenarioFeatures: hideScenarioFeaturesProp,
}) => {
  const { dataset } = useParams();

  const { data: tableData } = useQuery(
    getDatasetQueryOptions(dataset, 0, [], [], "", { enabled: false }),
  );

  const isData = Boolean(tableData?.data?.result?.table?.length);
  const isSyntheticDataset = Boolean(tableData?.data?.result?.synthetic_dataset);
  const plusRef = useRef(null);
  const compareRef = useRef(null);
  const theme = useTheme();
  const navigate = useNavigate();
  const [queryParamState] = useSearchParams({
    tab: "",
  });
  const location = useLocation();

  const { dataset: dataSetId } = useParams();
  const [compareDatasetDrawerVisible, setCompareDatasetDrawerVisible] =
    useState(false);
  const [baseColumnDrawerVisible, setBaseColumnDrawerVisible] = useState(false);
  const [selectedDatasets, setSelectedDatasets] = useState([]);
  const [selectedDatasetsValues, setSelectedDatasetsValues] = useState([]);
  const [openShare, setOpenShare] = useState(false);
  const [openConfigure, setOpenConfigure] = useState(false);

  // Check if we're coming from a scenario (using URL params for reliability)
  const urlParams = new URLSearchParams(location.search);
  const fromScenario = urlParams.get("fromScenario") === "true";
  const scenarioName = urlParams.get("scenarioName");
  const scenarioId = urlParams.get("scenarioId");
  const queryClient = useQueryClient();

  const { role } = useAuthContext();
  // Use prop if provided, otherwise check URL params
  const hideScenarioFeatures = hideScenarioFeaturesProp ?? fromScenario;

  const { initiateCreateMode } = useRunExperimentStoreShallow((state) => ({
    initiateCreateMode: state.initiateCreateMode,
  }));
  const { setOpenRunOptimization } = useRunOptimizationStore();
  const { isOSS } = useDeploymentMode();
  const { processingComplete } = useDatasetOriginStore();
  const { isProcessingData } = useProcessingStore();

  useEffect(() => {
    if (!compareFromOutside) {
      setSelectedDatasetsValuesInParent(selectedDatasets);
      setSelectedDatasetData(selectedDatasetsValues);
    }
  }, [selectedDatasets]);

  const handleSelectedDatasets = (selected, datasets) => {
    setSelectedDatasets(selected);
    setSelectedDatasetsValues(datasets);
  };

  const onCompareDatasetDrawerClose = () => {
    setCompareDatasetDrawerVisible(false);
  };
  const onBaseColumnDrawerClose = () => {
    setBaseColumnDrawerVisible(false);
  };

  const {
    data: datasetList,
    isLoading: isDatasetListLoading,
    error: datasetListError,
    refetch: refetchData,
  } = useDevelopDatasetList();

  const {
    data: scenariosList,
    isLoading: isScenariosListLoading,
    refetch: refetchScenarios,
    error: scenarioErrors,
  } = useScenariosList("", {
    enabled: Boolean(fromScenario && scenarioId),
  });

  useEffect(() => {
    refetchData();
  }, [dataSetId, refetchData]);

  const datasetOptions = useMemo(
    () =>
      datasetList?.map(({ datasetId, name }) => ({
        label: name,
        value: datasetId,
      })),
    [datasetList],
  );

  const scenarioOptions = useMemo(
    () =>
      scenariosList?.map(({ id, name, scenarioType, dataset }) => ({
        label: name,
        value: id,
        scenarioType,
        scenarioDatasetId: dataset,
      })),
    [scenariosList],
  );

  const handleBackClick = () => {
    if (isProcessingData) {
      // invalidate dataset queries
      queryClient.invalidateQueries({
        queryKey: ["dataset-detail"],
      });
    }

    const previousPath = location.state?.from;
    const currentPath = location.pathname;
    const currentBaseSection = currentPath.split("/").slice(0, 3).join("/");

    if (shouldHideDevelopBar) {
      const datasetId = currentPath.split("/")[3];
      navigate(`/dashboard/develop/${datasetId}?tab=data`);
      return;
    }

    // If coming from a scenario, go back to scenarios
    if (fromScenario && scenarioId) {
      navigate("/dashboard/simulate/scenarios");
      return;
    }

    if (previousPath && previousPath.startsWith(currentBaseSection)) {
      navigate(-1);
    } else {
      navigate(`/dashboard/develop`);
    }
  };

  const renderValue = (value) => {
    const options = fromScenario ? scenarioOptions : datasetOptions;
    const emptyText = fromScenario ? "Select a scenario" : "Select a dataset";

    if (!options) return emptyText;

    const selectedOption = options.find((option) => option.value === value);
    if (!selectedOption) return emptyText;

    const label = selectedOption.label;
    const maxLength = 20;

    return label.length > maxLength ? `${label.slice(0, maxLength)}...` : label;
  };

  // const buttonStyles = {
  //   color: theme.palette.text.primary,
  //   border: "1px solid",
  //   fontSize: "12px",
  //   fontWeight: 400,
  //   borderColor: theme.palette.divider,
  //   paddingY: theme.spacing(2),
  //   paddingX: theme.spacing(1.5),
  // };

  const actionButtons = hideScenarioFeatures
    ? []
    : [
        {
          icon: "ic_experiment",
          title: "Experiment",
          action: () => initiateCreateMode(true),
          hoverText: "Configure multiple prompts, models, and evaluations",
          link: "https://docs.futureagi.com/docs/dataset/features/experiments",
          // event: Events.runExperimentClicked,
        },
        {
          icon: "ic_optimize",
          title: "Optimize",
          hoverText: isOSS
            ? " Not available on self host"
            : "Improve your prompt’s accuracy, reduce errors, enhance output quality",
          link: "https://docs.futureagi.com/docs/cookbook/quickstart/dataset-optimization",
          action: () => setOpenRunOptimization(true),
          disabled: isOSS,
          // event: Events.optimizeClicked,
        },
        {
          icon: "ic_configure",
          title: "Configure",
          action: () => setOpenConfigure(true),
          event: null,
        },
        {
          icon: "ic_share",
          title: "Share",
          hoverText: "Share to give others access to the selected dataset",
          action: () => setOpenShare(true),
          event: null,
        },
      ];

  return (
    <>
      <Box
        sx={{
          paddingTop: theme.spacing(2),
          paddingX: theme.spacing(1),
          paddingRight: theme.spacing(2.5),
          display: "flex",
          gap: theme.spacing(2),
          alignItems: "center",
        }}
      >
        <BackButton onBack={handleBackClick} />

        {/* Show scenario info if coming from a scenario */}
        {/* {fromScenario && scenarioName && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              px: 2,
              py: 0.5,
              bgcolor: "primary.lighter",
              borderRadius: 1,
              border: "1px solid",
              borderColor: "primary.light",
            }}
          >
            <Iconify icon="mdi:hexagon-outline" width={20} />
            <Box>
              <Box sx={{ fontSize: "12px", color: "text.secondary" }}>
                Scenario
              </Box>
              <Box sx={{ fontSize: "14px", fontWeight: 500 }}>
                {scenarioName}
              </Box>
            </Box>
          </Box>
        )} */}

        {!isCompareDataset ? (
          <>
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: theme.spacing(2),
              }}
            >
              {datasetListError || scenarioErrors ? (
                <Alert
                  severity="error"
                  action={
                    <Button
                      color="inherit"
                      size="small"
                      onClick={() => {
                        if (fromScenario) {
                          refetchScenarios();
                        } else {
                          refetchData();
                        }
                      }}
                    >
                      Retry
                    </Button>
                  }
                  sx={{ minWidth: 200 }}
                >
                  Failed to load datasets
                </Alert>
              ) : isDatasetListLoading || isScenariosListLoading ? (
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    minWidth: 200,
                    height: "30px",
                    px: 2,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    backgroundColor: "background.neutral",
                  }}
                >
                  <CircularProgress size={16} />
                  <Box sx={{ color: "text.secondary", fontSize: "14px" }}>
                    {fromScenario
                      ? "Loading scenarios..."
                      : "Loading datasets..."}
                  </Box>
                </Box>
              ) : hideScenarioFeatures ? (
                // Show scenarios link when in scenario context
                <DropdownWithSearch
                  value={scenarioId}
                  setValue={() => {}}
                  options={scenarioOptions || []}
                  renderValue={renderValue}
                  anchorRef={plusRef}
                  ref={plusRef}
                  popoverComponent={(props) => (
                    <DatasetSelectDropDown
                      {...props}
                      fetchOptions={useScenariosList}
                      searchPlaceholder="Search Scenarios"
                      labelText="All Scenarios"
                      onSelect={(value) => {
                        if (
                          value?.scenarioType === "dataset" &&
                          value?.scenarioDatasetId &&
                          value?.value !== scenarioId
                        ) {
                          // Preserve scenario context when switching datasets
                          const newParams = new URLSearchParams();
                          // newParams.set("tab", queryParamState?.tab || "data");
                          if (fromScenario) {
                            newParams.set("fromScenario", "true");
                            newParams.set("scenarioId", value?.value);
                            newParams.set("scenarioName", value?.label);
                          }
                          // navigate(`/dashboard/simulate/scenarios/${value?.value}`);
                          navigate(
                            `/dashboard/develop/${value?.scenarioDatasetId}?${newParams.toString()}`,
                          );
                        }
                      }}
                      ref={plusRef}
                    />
                  )}
                  sx={{ width: "fit-content" }}
                  useCustomStyle={true}
                  size={undefined}
                  label={undefined}
                  multiple={undefined}
                  onSelect={() => {}}
                  onClose={undefined}
                  anchorElement={undefined}
                  iconUrl={undefined}
                  labelSx={undefined}
                />
              ) : (
                <DropdownWithSearch
                  value={dataSetId}
                  setValue={() => {}}
                  options={datasetOptions || []}
                  renderValue={renderValue}
                  anchorRef={plusRef}
                  ref={plusRef}
                  popoverComponent={(props) => (
                    <DatasetSelectDropDown
                      {...props}
                      fetchOptions={useDevelopDatasetList}
                      searchPlaceholder="Search Dataset"
                      labelText="All Datasets"
                      onSelect={(value) => {
                        // Preserve scenario context when switching datasets
                        const newParams = new URLSearchParams();
                        newParams.set("tab", queryParamState?.tab || "data");
                        if (fromScenario) {
                          newParams.set("fromScenario", "true");
                          newParams.set("scenarioId", scenarioId);
                          newParams.set("scenarioName", scenarioName);
                        }
                        navigate(
                          `/dashboard/develop/${value.value}?${newParams.toString()}`,
                        );
                      }}
                      ref={plusRef}
                    />
                  )}
                  sx={{ width: "fit-content" }}
                  useCustomStyle={true}
                  size={undefined}
                  label={undefined}
                  multiple={undefined}
                  onSelect={() => {}}
                  onClose={undefined}
                  anchorElement={undefined}
                  iconUrl={undefined}
                  labelSx={undefined}
                />
              )}
            </Box>
            {!shouldHideDevelopBar && !hideScenarioFeatures && (
              <CustomTooltip
                show={true}
                title="Compare with another dataset"
                placement="top"
                arrow
                size="small"
              >
                <Button
                  ref={compareRef}
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    setCompareDatasetDrawerVisible(true);
                    trackEvent(Events.datasetCompareClicked, {
                      [PropertyName.id]: dataSetId,
                    });
                  }}
                  disabled={
                    !isData || (isSyntheticDataset && !processingComplete)
                  }
                  startIcon={
                    <SvgColor
                      src="/assets/icons/custom/compare.svg"
                      // sx={{ width: "16px", height: "16px", color: theme.palette.text.primary }}
                    />
                  }
                  // sx={buttonStyles}
                >
                  Compare
                </Button>
              </CustomTooltip>
            )}
            <Box
              sx={{
                display: "flex",
                gap: theme.spacing(1.5),
                marginLeft: "auto",
                alignItems: "center",
              }}
            >
              {currentTab === "data" &&
                !shouldHideDevelopBar &&
                actionButtons.map((button, index) => (
                  <CustomTooltip
                    key={index}
                    show={button.hoverText ? true : false}
                    title={
                      <Box>
                        {button.hoverText}{" "}
                        {button.link && (
                          <Link
                            href={button.link}
                            underline="always"
                            color="blue.500"
                            target="_blank"
                            rel="noopener noreferrer"
                            fontWeight="fontWeightMedium"
                            onClick={(e) => e.stopPropagation()}
                            sx={{ whiteSpace: "nowrap" }}
                          >
                            Read more
                          </Link>
                        )}
                      </Box>
                    }
                    placement="bottom"
                    arrow
                    size="small"
                    type="black"
                    slotProps={{
                      tooltip: {
                        sx: {
                          maxWidth: "200px !important",
                        },
                      },
                    }}
                  >
                    <Button
                      key={index}
                      size="small"
                      variant="outlined"
                      startIcon={
                        <SvgColor
                          src={`/assets/icons/action_buttons/${button.icon}.svg`}
                          // sx={{ width: "16px", height: "16px", color: theme.palette.text.primary }}
                        />
                      }
                      disabled={
                        button.disabled ||
                        !isData ||
                        (isSyntheticDataset && !processingComplete) ||
                        !RolePermission.DATASETS[PERMISSIONS.UPDATE][role]
                      }
                      onClick={() => {
                        button.action?.();
                        if (button?.event) {
                          trackEvent(button.event, {
                            [PropertyName.id]: dataSetId,
                          });
                        }
                      }}
                      sx={{
                        "&:hover": {
                          backgroundColor: `${theme.palette.divider} !important`,
                        },
                      }}
                      // sx={buttonStyles}
                    >
                      {button.title}
                    </Button>
                  </CustomTooltip>
                ))}
            </Box>
            <Share
              open={openShare}
              onClose={() => setOpenShare(false)}
              body="Share this link to give others access to the selected dataset. Anyone with the link will be able to view the data."
            />
            <ConfigureDatasetModal
              open={openConfigure}
              datasetName={
                datasetOptions?.filter(
                  (datasets) => datasets?.value === dataSetId,
                )[0]?.label
              }
              onClose={() => setOpenConfigure(false)}
            />
          </>
        ) : (
          <CompareDatasetsTop
            datasetOptions={datasetOptions}
            setIsCompareDataset={setIsCompareDataset}
            setSelectedDatasetData={setSelectedDatasetData}
            setSelectedDatasetsValuesInParent={
              setSelectedDatasetsValuesInParent
            }
            setBaseColumn={setBaseColumnInParent}
            setIsChooseWinnerSelected={setIsChooseWinnerSelected}
            selectedDatasetData={selectedDatasetData}
            setCurrentTab={setCurrentTab}
            setCompareFromOutSide={setCompareFromOutSide}
            setIsChooseWinnerButtonVisible={setIsChooseWinnerButtonVisible}
            setIsCommonColumn={setIsCommonColumn}
          />
        )}
      </Box>
      <CompareDatasetDrawer
        onCompareDatasetDrawerClose={onCompareDatasetDrawerClose}
        compareDatasetDrawerVisible={compareDatasetDrawerVisible}
        datasetOptions={datasetOptions}
        setCompareDatasetDrawerVisible={setCompareDatasetDrawerVisible}
        setBaseColumnDrawerVisible={setBaseColumnDrawerVisible}
        onSelectedDatasetsChange={handleSelectedDatasets}
      />
      {selectedDatasets.length ? (
        <BaseColumnDrawer
          setIsCompareDataset={setIsCompareDataset}
          baseColumnDrawerVisible={baseColumnDrawerVisible}
          selectedDatasets={selectedDatasets}
          onBaseColumnDrawerClose={onBaseColumnDrawerClose}
          onCompareDatasetDrawerClose={onCompareDatasetDrawerClose}
          selectedValue={baseColumn}
          setSelectedValue={setBaseColumnInParent}
          setCurrentTab={setCurrentTab}
          setIsCommonColumn={setIsCommonColumn}
          selectedDatasetsValues={selectedDatasetsValues}
        />
      ) : null}
    </>
  );
};

DatasetsSelectRow.propTypes = {
  isCompareDataset: PropTypes.bool,
  setCurrentTab: PropTypes.func,
  currentTab: PropTypes.string,
  setIsChooseWinnerSelected: PropTypes.func,
  baseColumn: PropTypes.string,
  setSelectedDatasetData: PropTypes.func,
  setBaseColumnInParent: PropTypes.func,
  setSelectedDatasetsValuesInParent: PropTypes.func,
  setIsCompareDataset: PropTypes.func,
  selectedDatasetData: PropTypes.array,
  compareFromOutside: PropTypes.bool,
  setCompareFromOutSide: PropTypes.func,
  setIsCommonColumn: PropTypes.func,
  setIsChooseWinnerButtonVisible: PropTypes.func,
  shouldHideDevelopBar: PropTypes.bool,
  isData: PropTypes.bool,
  hideScenarioFeatures: PropTypes.bool,
};
export default DatasetsSelectRow;
