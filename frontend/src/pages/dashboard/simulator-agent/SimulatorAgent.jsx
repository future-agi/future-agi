import { Box, Button, Typography, Link } from "@mui/material";
import React, { useCallback, useMemo, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import DeleteSimulatorAgentDialog from "src/components/simulator-agent/DeleteSimulatorAgentDialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { Xwrapper } from "react-xarrows";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { ShowComponent } from "src/components/show";
import SimulatorAgentForm from "src/components/simulator-agent-form/SimulatorAgentForm";
import { enqueueSnackbar } from "notistack";
import {
  Events,
  handleOnDocsClicked,
  PropertyName,
  trackEvent,
} from "src/utils/Mixpanel";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { useDebounce } from "src/hooks/use-debounce";
import { AgGridReact } from "ag-grid-react";
import { getSimulatorAgentColumnDefs } from "./common";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useAuthContext } from "src/auth/hooks";

const newFormDefaultValues = {
  name: "",
  prompt: "",
  model: "",
  agentType: "",
  llmTemperature: 0.7,
  voiceProvider: "elevenlabs",
  voiceName: "marissa",
  interruptSensitivity: 5,
  conversationSpeed: 1,
  finishedSpeakingSensitivity: 5,
  maxCallDurationInMinutes: 5,
  initialMessageDelay: 1,
  initialMessage: "",
};

function SimulatorAgent() {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { role } = useAuthContext();
  const [searchQuery, setSearchQuery] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [agent, setAgent] = useState(null);
  const queryClient = useQueryClient();
  const [defaultValues, setDefaultValues] = useState(null);
  const gridApiRef = useRef(null);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const { data } = useQuery({
    queryKey: ["simulatorAgents", 1, 10, debouncedSearchQuery],
    queryFn: () =>
      axios.get(endpoints.simulatorAgents.list, {
        params: {
          page: 1,
          limit: 10,
          search: debouncedSearchQuery,
        },
      }),
    select: (d) => d.data,
  });

  const datasource = useMemo(() => {
    return {
      getRows: async (params) => {
        const { request } = params;
        const pageSize = request ? request?.endRow - request?.startRow : 10;
        const pageNumber = Math.floor((request?.startRow ?? 1) / pageSize) + 1;

        const { data } = await queryClient.fetchQuery({
          queryKey: ["scenarios", pageNumber, pageSize, debouncedSearchQuery],
          queryFn: () => {
            return axios.get(endpoints.simulatorAgents.list, {
              params: {
                page: pageNumber,
                limit: 10,
                search: debouncedSearchQuery,
              },
            });
          },
        });

        const rows = (data?.results ?? []).map((row) => ({
          ...row,
          testDetail: {
            name: row.name,
            description: row.description,
            scenarios: row.scenarios,
          },
        }));
        try {
          params.success({
            rowData: rows,
            rowCount: data?.count ?? 1,
          });
        } catch (error) {
          params.fail();
        }
      },
    };
  }, [debouncedSearchQuery, queryClient]);

  const defaultColDef = {
    lockVisible: true,
    sortable: false,
    filter: false,
    resizable: true,
    minWidth: 150,
    suppressMenuHide: true,
    suppressHeaderMenuButton: true,
    suppressHeaderContextMenu: true,
  };
  const refreshGrid = () => {
    if (gridApiRef.current) {
      gridApiRef.current.api.refreshServerSide({ purge: true });
    }
  };

  const { mutate: addAgent } = useMutation({
    mutationFn: (data) => axios.post(endpoints.simulatorAgents.create, data),
    onSuccess: () => {
      refreshGrid();
      enqueueSnackbar({
        message: "Agent created successfully",
        variant: "success",
      });
      setDefaultValues(null);
    },
  });

  const { mutate: editAgent } = useMutation({
    mutationFn: (data) =>
      axios.put(endpoints.simulatorAgents.edit(agent.id), data),
    onSuccess: () => {
      refreshGrid();
      enqueueSnackbar({
        message: "Agent edited successfully",
        variant: "success",
      });
      setDefaultValues(null);
    },
  });

  const hasData = useMemo(() => {
    if (!data) return null;
    return data?.results?.length > 0;
  }, [data]);

  const handleRowClick = ({ row }) => {
    if (!row?.id) return;

    setAgent(row);
    setDefaultValues(row);
    trackEvent(Events.simulatorAgentConversationClicked, {
      [PropertyName.id]: row?.id,
    });
  };

  const handleAddAgent = () => {
    setDefaultValues(newFormDefaultValues);
    trackEvent(Events.simulatorAgentAddClicked, {
      [PropertyName.click]: true,
    });
  };

  const handleEditAgent = useCallback((agent) => {
    setDefaultValues(agent);
    setAgent(agent);
  }, []);

  const handleDeleteAgent = useCallback((agent) => {
    setAgent(agent);
    setDeleteDialogOpen(true);
  }, []);

  const columnDefs = useMemo(
    () => getSimulatorAgentColumnDefs(handleEditAgent, handleDeleteAgent, role),
    [handleEditAgent, handleDeleteAgent, role],
  );

  const handleCloseDeleteDialog = () => {
    setDeleteDialogOpen(false);
    setAgent(null);
  };

  function handleSubmit(data) {
    if (defaultValues.name === "") {
      addAgent(data);
      trackEvent(Events.simulatorAgentCreatesimClicked, {
        [PropertyName.formFields]: data,
      });
    } else {
      editAgent(data);
      trackEvent(Events.simulatorAgentEditsimSubmitted, {
        [PropertyName.id]: agent?.id,
        [PropertyName.formFields]: data,
      });
    }
  }

  return (
    <>
      <Helmet>
        <title>Simulator Agents</title>
      </Helmet>

      <Xwrapper>
        <Box
          sx={{
            backgroundColor: "background.paper",
            height: "100%",
            padding: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
            <Typography
              color="text.primary"
              typography="m2"
              fontWeight={"fontWeightSemiBold"}
            >
              Simulator Agents
            </Typography>
            <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
              <Typography
                typography="s1"
                color="text.primary"
                fontWeight={"fontWeightRegular"}
              >
                Create and manage simulator agents for voice conversations.
                Click{" "}
                <Link
                  onClick={() => handleOnDocsClicked("simulator_agents_page")}
                  href="https://docs.futureagi.com/docs/simulation"
                  target="_blank"
                >
                  Docs
                </Link>{" "}
                for more info.
              </Typography>
            </Box>
          </Box>

          <ShowComponent condition={!hasData && debouncedSearchQuery === ""}>
            <Box
              sx={{
                display: "block",
                flex: 1,
              }}
            >
              <EmptyLayout
                title="Add your first simulator agent"
                description="Create simulator agents to handle voice conversations with custom prompts and voice settings."
                link="https://docs.futureagi.com"
                linkText="Check docs"
                onLinkClick={() => handleOnDocsClicked("simulator_agents_page")}
                action={
                  <Button
                    variant="contained"
                    color="primary"
                    sx={{
                      px: "24px",
                    }}
                    startIcon={
                      <Iconify
                        width={20}
                        icon="solar:add-linear"
                        color="background.paper"
                      />
                    }
                    disabled={
                      !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                    }
                    onClick={handleAddAgent}
                  >
                    <Typography
                      typography="s1"
                      fontWeight={"fontWeightSemiBold"}
                    >
                      Add Simulator Agent
                    </Typography>
                  </Button>
                }
                icon="/assets/icons/navbar/hugeicons.svg"
              />
            </Box>
          </ShowComponent>

          <ShowComponent condition={hasData || !!debouncedSearchQuery}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                gap: 2,
              }}
            >
              <Box
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <FormSearchField
                  size="small"
                  placeholder="Search"
                  sx={{
                    minWidth: "250px",
                    "& .MuiOutlinedInput-root": { height: "30px" },
                  }}
                  searchQuery={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  InputProps={{}}
                />
                <Box>
                  <Button
                    variant="contained"
                    color="primary"
                    sx={{
                      px: "24px",
                    }}
                    startIcon={
                      <Iconify
                        width={20}
                        icon="solar:add-linear"
                        color="background.paper"
                      />
                    }
                    disabled={
                      !RolePermission.SIMULATION_AGENT[PERMISSIONS.CREATE][role]
                    }
                    onClick={handleAddAgent}
                  >
                    <Typography typography="s1" fontWeight={"fontWeightMedium"}>
                      Add Simulator Agent
                    </Typography>
                  </Button>
                </Box>
              </Box>

              <Box
                display={"flex"}
                flexDirection={"column"}
                overflow={"auto"}
                height={"100%"}
                sx={{
                  "& .ag-cell p": {
                    lineHeight: 1.5,
                    my: 0.25,
                  },
                  "& .ag-cell-wrapper": {
                    lineHeight: 1.5,
                  },
                  "& .ag-cell": {
                    "&::-webkit-scrollbar": {
                      display: "none",
                    },
                    "-ms-overflow-style": "none", // IE and Edge
                    "scrollbar-width": "none", // Firefox
                  },
                  "& .MuiBox-root": {
                    "&::-webkit-scrollbar": {
                      display: "none",
                    },
                    "-ms-overflow-style": "none", // IE and Edge
                    "scrollbar-width": "none", // Firefox
                  },
                }}
              >
                <AgGridReact
                  ref={gridApiRef}
                  theme={agTheme}
                  getRowHeight={(params) => {
                    return params.node.rowPinned === "bottom" ? 30 : 70;
                  }}
                  rowModelType="serverSide"
                  suppressContextMenu
                  suppressServerSideFullWidthLoadingRow={true}
                  maxBlocksInCache={1}
                  cacheBlockSize={10}
                  serverSideDatasource={datasource}
                  serverSideInitialRowCount={10}
                  columnDefs={columnDefs}
                  defaultColDef={defaultColDef}
                  getRowId={(params) => params.data.id}
                  pagination={true}
                  paginationPageSizeSelector={false}
                  rowStyle={{ cursor: "pointer" }}
                  onCellClicked={(event) => {
                    const colId = event.column.getColId();

                    if (colId === "actions") {
                      return;
                    }
                    if (
                      RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role]
                    ) {
                      handleRowClick?.({ row: event.data });
                    }
                  }}
                />
              </Box>
            </Box>
          </ShowComponent>
        </Box>
      </Xwrapper>

      <DeleteSimulatorAgentDialog
        open={deleteDialogOpen}
        onClose={handleCloseDeleteDialog}
        agent={agent}
        onDeleteSuccess={refreshGrid}
      />

      <SimulatorAgentForm
        open={Boolean(defaultValues)}
        onClose={() => {
          setDefaultValues(null);
          setAgent(null);
        }}
        defaultValues={defaultValues}
        heading={`${!agent ? "Create" : "Edit"} agent simulator`}
        subHeading="Configure an AI agent simulator with voice, conversation, and behavior settings"
        onSubmit={handleSubmit}
        saveLabel={`${!agent ? "Create" : "Edit"} Simulator`}
      />
    </>
  );
}

export default SimulatorAgent;
