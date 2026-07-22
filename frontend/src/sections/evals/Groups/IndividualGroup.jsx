import {
  Avatar,
  Box,
  Button,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  NavLink,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import Iconify from "src/components/iconify";
import { format, isValid, parseISO } from "date-fns";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { stringAvatar } from "src/utils/utils";
import SvgColor from "src/components/svg-color";
import EditIndividualGroupDetailsDialog from "./EditIndividualGroupDetailsDialog";
import { useEvalStore } from "../store/useEvalStore";
import DeleteEvalFromGroupDialog from "./DeleteEvalFromGroupDialog";
import { useDebounce } from "src/hooks/use-debounce";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { useEvaluationContext } from "../../common/EvaluationDrawer/context/EvaluationContext";
import { ShowComponent } from "../../../components/show/ShowComponent";
import logger from "../../../utils/logger";
import CustomTooltip from "src/components/tooltip";

const IndividualGroup = ({ groupId, onReset }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { id: paramsGroupId } = useParams();
  const id = groupId || paramsGroupId;
  const apiref = useRef();
  const [searchQuery, setSearchQuery] = useState("");
  const [_, setSearchParams] = useSearchParams();
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);
  const { setSelectedEvals, setCreateGroupMode, setEditGroupMode } =
    useEvalStore();
  const navigate = useNavigate();
  const { setVisibleSection, setSelectedEval, setCurrentTab } =
    useEvaluationContext();
  const [currentRowCount, setCurrentRowCount] = useState(0);

  // Only fetch basic group info, not the members
  const {
    data: evalGroupBasicData,
    isLoading: isLoadingBasicData,
    refetch: refetchEvalGroup,
  } = useQuery({
    queryKey: ["eval-group-basic", id],
    queryFn: async () => {
      const response = await axios.get(
        `${endpoints.develop.eval.groupEvals}${id}/`,
      );
      return response.data;
    },
    enabled: !!id,
    select: (data) => {
      const payload = evalGroupDetailPayload(data);
      return {
        evalGroup: payload?.eval_group,
        lastUpdated: payload?.eval_group?.updated_at,
        rowCount:
          (payload?.members || payload?.eval_group?.members)?.length || 0,
      };
    },
  });

  const [selectDrawerType, setSelectedDrawerType] = useState({
    type: null,
    id: null,
  });

  const columnDefs = [
    {
      headerName: "Evaluation List",
      flex: 2,
      cellRenderer: ({ data }) => (
        <Box
          height="100%"
          display="flex"
          flexDirection="column"
          justifyContent="center"
        >
          <Typography
            sx={{ fontSize: "14px", fontWeight: 500, color: "text.primary" }}
          >
            {data?.name}
          </Typography>
          <Typography
            sx={{ fontSize: "12px", fontWeight: 400, color: "text.disabled" }}
          >
            {data?.description}
          </Typography>
        </Box>
      ),
    },
    {
      headerName: "Added By",
      field: "added_by",
      cellRenderer: ({ value }) => {
        const addedBy = value || "-";
        return (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              height: "100%",
              gap: (theme) => theme.spacing(0.5),
            }}
          >
            <Avatar
              variant="rounded"
              {...stringAvatar(addedBy)}
              sx={{
                width: (theme) => theme.spacing(3),
                height: (theme) => theme.spacing(3),
                fontSize: "11px",
                color: "pink.500",
                backgroundColor: "background.neutral",
              }}
            />
            <Typography typography="s2" fontWeight={"fontWeightRegular"}>
              {addedBy}
            </Typography>
          </Box>
        );
      },
    },
    {
      headerName: "Date Added",
      field: "added_on",
      cellRenderer: ({ value }) => {
        const parsedDate = parseISO(value);
        const formattedDate = isValid(parsedDate)
          ? format(parsedDate, "dd/MM/yyyy")
          : "-";

        return (
          <Box height={"100%"} display={"flex"} alignItems={"center"}>
            {formattedDate}
          </Box>
        );
      },
    },
    {
      headerName: "Actions",
      field: "actions",
      cellRenderer: (params) => (
        <Box height={"100%"} display={"flex"} alignItems={"center"}>
          <CustomTooltip
            show={evalGroupBasicData?.evalGroup?.is_sample}
            placement="top"
            title="This evaluation group is pre-defined based on use case and cannot be customized"
          >
            <span style={{ display: "inline-block" }}>
              <IconButton
                sx={{
                  border: "1px solid",
                  borderRadius: "2px",
                  borderColor: "divider",
                  padding: 0.5,
                }}
                disabled={evalGroupBasicData?.evalGroup?.is_sample}
                onClick={() => {
                  const rowId = params.data.eval_template_id;
                  setSelectedDrawerType({
                    type: "delete",
                    id: rowId,
                  });
                }}
              >
                <SvgColor
                  src="/assets/icons/ic_delete.svg"
                  sx={{
                    width: 19,
                    height: 19,
                    color: evalGroupBasicData?.evalGroup?.is_sample
                      ? "divider"
                      : "text.disabled",
                  }}
                />
              </IconButton>
            </span>
          </CustomTooltip>
        </Box>
      ),
    },
  ];

  const defaultColDef = {
    lockVisible: true,
    sortable: false,
    filter: false,
    minWidth: 150,
    suppressMenuHide: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };

  // Move API call inside getDataSource for proper loading states
  const getDataSource = useCallback(() => {
    return {
      getRows: async (params) => {
        try {
          // Show loading state
          const response = await axios.get(
            `${endpoints.develop.eval.groupEvals}${id}/`,
            {
              params: {
                ...(debouncedSearchQuery && {
                  name: debouncedSearchQuery,
                }),
              },
            },
          );

          const members = evalGroupDetailPayload(response.data)?.members || [];

          // Success callback with data
          params.success({
            rowData: members,
            rowCount: members.length,
          });
        } catch (error) {
          logger.error("Error fetching grid data:", error);
          // Fail callback for error handling
          params.fail();
        }
      },
    };
  }, [id, debouncedSearchQuery]);

  // Update grid when search query changes
  useEffect(() => {
    if (apiref.current?.api) {
      const dataSource = getDataSource();
      apiref.current.api.setGridOption("serverSideDatasource", dataSource);
    }
  }, [getDataSource]);

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource();
      params.api.setGridOption("serverSideDatasource", dataSource);
    },
    [getDataSource],
  );

  const handleEditGroupList = async () => {
    try {
      // Fetch current members for editing
      const response = await axios.get(
        `${endpoints.develop.eval.groupEvals}${id}/`,
      );
      const members = evalGroupDetailPayload(response.data)?.members || [];

      setSelectedEvals(
        members?.map((member) => ({
          id: member?.eval_template_id,
          name: member?.name,
          evalTemplateTags: member?.tags,
          description: member?.description,
          type: member?.tags?.includes("FUTURE_EVALS")
            ? "futureagi_built"
            : "user_built",
          ...member,
        })),
      );

      if (groupId) {
        setSearchParams((prev) => ({
          ...Object.fromEntries(prev),
          "group-id": groupId,
        }));
        onReset();
        setCurrentTab("evals");
        setEditGroupMode(true);
        setCreateGroupMode(true);
      } else {
        setCreateGroupMode(true);
        setEditGroupMode(true);
        navigate(`/dashboard/evaluations?group-id=${id}`);
      }
    } catch (error) {
      logger.error("Error fetching group members for editing:", error);
    }
  };
  // Refresh grid data
  const handleRefresh = () => {
    if (apiref.current?.api) {
      apiref.current.api.refreshServerSide();
    }
    refetchEvalGroup(); // Also refresh basic group data
  };

  const parsedDate = parseISO(
    evalGroupBasicData?.last_updated ?? evalGroupBasicData?.lastUpdated,
  );
  const formatted = isValid(parsedDate)
    ? `Last saved ${format(parsedDate, "MMM d, yyyy 'at' h:mm a")}`
    : "-";

  const handleeRunEvaluations = (e) => {
    e.stopPropagation();
    setVisibleSection("mapping");
    setSelectedEval({
      id: id,
      name: evalGroupBasicData?.evalGroup?.name,
      evalTemplateName: evalGroupBasicData?.evalGroup?.name,
      description: evalGroupBasicData?.evalGroup?.description,
      isGroupEvals: true,
    });
  };

  // onModelUpdated fires whenever the grid’s row model is refreshed
  const onModelUpdated = useCallback(() => {
    if (apiref.current?.api) {
      const count = apiref.current.api.getDisplayedRowCount();
      setCurrentRowCount(count);
    }
  }, []);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <Stack
        display="flex"
        flexDirection="row"
        justifyContent="space-between"
        alignItems="center"
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <NavLink
            to="/dashboard/evaluations/groups"
            style={{
              textDecoration: "none",
              display: "flex",
              alignItems: "center",
            }}
            onClick={(e) => {
              if (typeof onReset === "function") {
                e.preventDefault();
                onReset();
              }
            }}
          >
            <Typography
              variant="s2"
              fontWeight={500}
              sx={{ textDecoration: "none" }}
              color="text.disabled"
            >
              Groups
            </Typography>
          </NavLink>

          <Iconify
            icon="fluent:chevron-right-12-regular"
            style={{ display: "flex", alignItems: "center" }}
          />
          {isLoadingBasicData ? (
            <Skeleton width={"100px"} height={"16px"} />
          ) : (
            <Typography
              sx={{
                fontSize: "14px",
                fontWeight: 500,
                display: "flex",
                alignItems: "center",
              }}
            >
              {evalGroupBasicData?.evalGroup?.name}
            </Typography>
          )}
        </Box>
        {isLoadingBasicData ? (
          <Skeleton width={"200px"} height={"16px"} />
        ) : (
          <Typography
            sx={{ fontSize: "11px", fontWeight: 400, color: "text.primary" }}
          >
            {formatted}
          </Typography>
        )}
      </Stack>

      <Stack display={"flex"} gap={1} paddingY={0.5}>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Typography sx={{ fontSize: "16px", fontWeight: "700" }}>
            {isLoadingBasicData ? (
              <Skeleton width={200} />
            ) : (
              <>Group Name : {evalGroupBasicData?.evalGroup?.name}</>
            )}
          </Typography>
          <CustomTooltip
            show={evalGroupBasicData?.evalGroup?.is_sample}
            placement="top"
            title="This evaluation group is pre-defined based on use case and cannot be customized"
          >
            <span style={{ display: "inline-block" }}>
              <IconButton
                disabled={
                  isLoadingBasicData || evalGroupBasicData?.evalGroup?.is_sample
                }
                sx={{
                  padding: 0.5,
                }}
                onClick={() => {
                  setSelectedDrawerType({
                    type: "edit",
                    id: evalGroupBasicData?.evalGroup?.id,
                  });
                }}
              >
                <SvgColor
                  src="/assets/icons/ic_edit_pencil.svg"
                  sx={{
                    width: 16,
                    height: 16,
                    color: evalGroupBasicData?.evalGroup?.is_sample
                      ? "text.disabled"
                      : "text.disabled",
                  }}
                />
              </IconButton>
            </span>
          </CustomTooltip>
        </Box>
        <Typography sx={{ whiteSpace: "normal", wordBreak: "break-word" }}>
          {isLoadingBasicData ? (
            <Skeleton width={"200px"} />
          ) : (
            evalGroupBasicData?.evalGroup?.description
          )}
        </Typography>
      </Stack>

      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <FormSearchField
          size="small"
          searchQuery={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search"
          sx={{ minWidth: "360px" }}
          disabled={isLoadingBasicData}
        />
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            gap: 1,
          }}
        >
          <CustomTooltip
            show={evalGroupBasicData?.evalGroup?.is_sample}
            placement="top"
            title="This evaluation group is pre-defined based on use case and cannot be customized"
          >
            <span style={{ display: "inline-block" }}>
              <Button
                variant="outlined"
                color="secondary"
                disabled={
                  isLoadingBasicData || evalGroupBasicData?.evalGroup?.is_sample
                }
                sx={{
                  px: "24px",
                  borderRadius: "8px",
                  height: "38px",
                  // border: "1px solid",
                }}
                startIcon={
                  <SvgColor
                    src="/assets/icons/ic_edit_pencil.svg"
                    sx={{
                      width: 20,
                      height: 20,
                      color: "primary.main",
                    }}
                  />
                }
                onClick={handleEditGroupList}
              >
                <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                  Edit List
                </Typography>
              </Button>
            </span>
          </CustomTooltip>
          <ShowComponent condition={!!groupId}>
            <Button
              variant="outline"
              color="secondary"
              disabled={isLoadingBasicData}
              sx={{
                px: "24px",
                borderRadius: "8px",
                height: "38px",
                border: "1px solid",
                borderColor: "primary.main",
                color: "primary.main",
              }}
              startIcon={
                <SvgColor
                  src="/assets/icons/ic_create-and-run.svg"
                  color="primary.main"
                  sx={{
                    width: "20px",
                    height: "20px",
                  }}
                />
              }
              onClick={handleeRunEvaluations}
            >
              <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                Run Group
              </Typography>
            </Button>
          </ShowComponent>
        </Box>
      </Box>

      <Typography sx={{ fontSize: "12px", fontWeight: 500 }}>
        {`All (${currentRowCount || 0})`}
      </Typography>

      <EditIndividualGroupDetailsDialog
        open={selectDrawerType?.type === "edit"}
        name={evalGroupBasicData?.evalGroup?.name}
        selectDrawerType={selectDrawerType}
        description={evalGroupBasicData?.evalGroup?.description}
        onClose={() => setSelectedDrawerType({ type: null, id: null })}
        handleRefresh={handleRefresh}
      />
      <DeleteEvalFromGroupDialog
        open={selectDrawerType?.type === "delete"}
        id={selectDrawerType?.id}
        groupId={evalGroupBasicData?.evalGroup?.id}
        handleRefresh={handleRefresh}
        onClose={() => setSelectedDrawerType({ type: null, id: null })}
        rowCount={evalGroupBasicData?.rowCount}
      />

      <Box sx={{ height: "600px", position: "relative" }}>
        <AgGridReact
          ref={apiref}
          theme={agTheme}
          getRowHeight={(params) =>
            params.node.rowPinned === "bottom" ? 30 : 70
          }
          defaultColDef={defaultColDef}
          columnDefs={columnDefs}
          cacheBlockSize={10}
          serverSideInitialRowCount={10}
          rowHeight={65}
          rowModelType="serverSide"
          suppressContextMenu
          getRowId={(params) => {
            return params?.data?.eval_template_id;
          }}
          pagination={false}
          paginationPageSizeSelector={false}
          rowStyle={{ cursor: "pointer" }}
          onGridReady={onGridReady}
          suppressServerSideFullWidthLoadingRow={true}
          onModelUpdated={onModelUpdated}
        />
      </Box>
    </Box>
  );
};

export default IndividualGroup;

IndividualGroup.propTypes = {
  groupId: PropTypes.string,
  onReset: PropTypes.func,
};

function evalGroupDetailPayload(data) {
  return data?.result || data;
}
