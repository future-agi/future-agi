import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Helmet } from "react-helmet-async";
import UserHeaders from "./UserHeaders";
import GridTable from "./GridTable";
import { getUserQueryOptions } from "./getUserQueryOptions";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  ProcessingStatusCell,
  ActionRender,
  OrgRoleCell,
  WorkspaceRoleCell,
  WorkspaceChipsCell,
  useCanSendInvite,
} from "./CellRender";
import AllActionForm from "./AllActionForm";
import WorkspaceDetailPanel from "./WorkspaceDetailPanel";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useLocation, useNavigate, useParams } from "react-router";
import BackButton from "src/sections/develop-detail/Common/BackButton";
import { LEVELS } from "./constant";
import { useUserManagementStore } from "./UserManagementStore";
import { endpoints } from "src/utils/axios";
import { gridSortModelToMemberListSort } from "./memberListGridQuery";

const UserManagementV2 = ({ workspaceScope = false }) => {
  const { workspaceId: workspaceIdParam } = useParams();
  let workspaceId = workspaceIdParam;

  const location = useLocation();
  const navigate = useNavigate();
  const { role, user, orgLevel, effectiveLevel } = useAuthContext();
  const { currentWorkspaceId } = useWorkspace();
  const queryClient = useQueryClient();
  const gridApiRef = useRef(null);
  const overlayTimeoutRef = useRef(null);
  const [inviteUser, setInviteUser] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedStatus, setSelectedStatus] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const { setUsersList } = useUserManagementStore();
  workspaceId = workspaceId ?? currentWorkspaceId ?? user?.default_workspace_id;

  // Use integer levels when available, fall back to string role check
  const canManageUsers =
    (orgLevel != null && orgLevel >= LEVELS.ADMIN) ||
    (effectiveLevel != null && effectiveLevel >= LEVELS.ADMIN) ||
    role === "Owner" ||
    role === "Admin" ||
    role === "workspace_admin";

  const { allowed: canSendInvite } = useCanSendInvite(orgLevel, effectiveLevel);

  const columnDefs = useMemo(
    () => [
      {
        headerName: "User name",
        field: "name",
        flex: 1,
        // Show expand chevron for master/detail rows in org-level view
        ...(!workspaceScope ? { cellRenderer: "agGroupCellRenderer" } : {}),
      },
      {
        headerName: "Organisation Role",
        field: "org_role",
        colId: "org_level",
        flex: 1,
        cellRenderer: OrgRoleCell,
      },
      // Org-level view: show workspace chips; workspace-scoped view: show single role
      ...(workspaceScope
        ? [
            {
              headerName: "Workspace Role",
              field: "ws_role",
              colId: "ws_level",
              flex: 1,
              cellRenderer: WorkspaceRoleCell,
            },
          ]
        : [
            {
              headerName: "Workspaces",
              field: "workspaces",
              flex: 1.5,
              cellRenderer: WorkspaceChipsCell,
              sortable: false,
            },
          ]),
      {
        headerName: "Email",
        field: "email",
        flex: 1,
      },
      {
        headerName: "Status",
        field: "status",
        flex: 1,
        cellRenderer: ProcessingStatusCell,
      },
      {
        headerName: "Start date",
        field: "created_at",
        flex: 1,
        valueFormatter: (params) =>
          params?.value ? format(new Date(params?.value), "dd/MM/yyyy") : "",
      },
      ...(canManageUsers
        ? [
            {
              headerName: " ",
              field: "action",
              width: 50,
              cellRenderer: ActionRender,
              cellRendererParams: { workspaceScope, workspaceId },
              sortable: false,
            },
          ]
        : []),
    ],
    [canManageUsers, workspaceScope],
  );

  // When workspaceScope is true, use workspace-specific member endpoint
  const wsEndpoint =
    workspaceScope && workspaceId
      ? endpoints.rbac.workspaceMemberList(workspaceId)
      : undefined;

  const getDataSource = (
    queryClient,
    overlayTimeoutRef,
    searchQuery,
    selectedStatus,
    selectedRole,
    workspaceId,
  ) => {
    return {
      getRows: async (params) => {
        const { request } = params;
        const pageNumber = Math.floor(request.startRow / 20);
        const sort = gridSortModelToMemberListSort(request?.sortModel, {
          workspaceScope,
        });
        const search = searchQuery || "";

        if (overlayTimeoutRef.current) {
          clearTimeout(overlayTimeoutRef.current);
          overlayTimeoutRef.current = null;
        }
        try {
          const queryOptions = getUserQueryOptions(
            {
              pageNumber,
              sort,
              search: search,
              filterStatus: selectedStatus ? [selectedStatus] : [],
              filterRole: selectedRole ? [selectedRole] : [],
              workspaceId,
              endpoint: wsEndpoint,
            },
            { staleTime: 5000 },
          );
          const data = await queryClient.fetchQuery({ ...queryOptions });
          const responseData = data?.data?.result || data?.data;
          const rows = responseData?.results || [];
          const totalRows = responseData?.total || rows.length;
          setUsersList(rows);
          params.api.setGridOption("context", {
            totalRowCount: totalRows,
          });

          params.success({
            rowData: rows,
            rowCount: totalRows,
          });
        } catch (e) {
          params.fail();
          overlayTimeoutRef.current = setTimeout(() => {
            params.api.showLoadingOverlay();
          }, 100);
        }
      },
    };
  };

  useEffect(() => {
    if (gridApiRef?.current?.api) {
      gridApiRef?.current?.api?.collapseAll();

      const dataSource = getDataSource(
        queryClient,
        overlayTimeoutRef,
        searchQuery,
        selectedStatus,
        selectedRole,
        workspaceId,
      );
      gridApiRef.current?.api?.setGridOption(
        "serverSideDatasource",
        dataSource,
      );
      // Optionally refresh the data
      gridApiRef?.current?.api?.refreshServerSide({ purge: true });
    }
  }, [searchQuery, selectedStatus, selectedRole, workspaceId, queryClient]);

  const onGridReady = useCallback(
    (params) => {
      const dataSource = getDataSource(
        queryClient,
        overlayTimeoutRef,
        searchQuery,
        selectedStatus,
        selectedRole,
        workspaceId,
      );
      params.api.setGridOption("serverSideDatasource", dataSource);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [queryClient, searchQuery, selectedStatus, selectedRole, workspaceId],
  );
  const pageTitle = useMemo(() => {
    // Only show workspace name when navigated via /workspace/:workspaceId
    if (workspaceIdParam && location?.state?.workspaceName) {
      return location.state.workspaceName;
    }
    return "Members";
  }, [workspaceIdParam, location]);

  return (
    <>
      <Helmet>
        <title>
          {workspaceScope ? "Workspace Members" : "User Management"}
        </title>
      </Helmet>
      <Box sx={{ paddingX: "2px" }}>
        {workspaceId && !workspaceScope && (
          <Box mb={2} display="flex" gap={2}>
            <BackButton onBack={() => navigate(-1)} />
          </Box>
        )}
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            {pageTitle}
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              fontWeight: "fontWeightRegular",
              color: "text.primary",
              marginTop: (theme) => theme.spacing(0.5),
            }}
          >
            {workspaceScope
              ? "Manage workspace members and their roles"
              : "Manage who has access to workspace"}
          </Typography>
        </Box>
        <Box
          sx={{
            paddingX: 0,
            paddingY: 2,
            display: "flex",
            gap: 2,
            width: "100%",
            justifyContent: "space-between",
          }}
        >
          <UserHeaders
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            selectedStatus={selectedStatus}
            setSelectedStatus={setSelectedStatus}
            selectedRole={selectedRole}
            setSelectedRole={setSelectedRole}
          />
          {(canSendInvite || canManageUsers) && (
            <Button
              variant="contained"
              color="primary"
              onClick={() => setInviteUser(true)}
            >
              Invite User
            </Button>
          )}
        </Box>
        <AllActionForm
          openActionForm={inviteUser ? { action: "invite-user" } : null}
          onClose={() => setInviteUser(false)}
          gridApi={gridApiRef?.current?.api}
          workspaceId={workspaceId}
        />
        {/* table data */}
        <Box
          sx={{
            height: `calc(100vh - ${workspaceId && !workspaceScope ? 210 : 160}px)`,
          }}
        >
          <GridTable
            // @ts-ignore
            onGridReady={onGridReady}
            ref={gridApiRef}
            columnDefs={columnDefs}
            otherGridOption={
              workspaceScope
                ? {}
                : {
                    masterDetail: true,
                    detailCellRenderer: WorkspaceDetailPanel,
                    detailRowAutoHeight: true,
                    keepDetailRows: true,
                    isRowMaster: (data) =>
                      data?.workspaces?.length > 0 && data?.status === "Active",
                  }
            }
          />
        </Box>
      </Box>
    </>
  );
};

UserManagementV2.propTypes = {
  workspaceScope: PropTypes.bool,
};

export default UserManagementV2;
