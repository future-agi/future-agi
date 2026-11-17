import React, { useEffect, useState } from "react";
import { DataGrid } from "@mui/x-data-grid";
import { Box, useTheme, Checkbox } from "@mui/material";
import PropTypes from "prop-types";
import CreateUserModal from "./CreateUserModal";
import { trackEvent, Events } from "src/utils/Mixpanel";
const columns = (palette, selectedRows, onUserSelect, handleRowSelect) => [
  {
    field: "checkbox",
    headerName: "",
    width: 50,
    sortable: false,
    cellClassName: "no-border",
    renderCell: (params) => (
      <Checkbox
        sx={{
          borderRight: "none",
        }}
        checked={selectedRows.includes(params.row.id)}
        onChange={(event) => {
          handleRowSelect(event.target.checked, params.row.id);
          trackEvent(Events.userSelected);
        }}
      />
    ),
  },
  { field: "name", headerName: "User Name", flex: 1, sortable: false },
  { field: "email", headerName: "Email", flex: 1, sortable: false },
  {
    field: "organization_role",
    headerName: "User Access",
    flex: 1,
    sortable: false,
  },
  // { field: "lastActive", headerName: "Last Active", flex: 1, sortable: false },
];

const UsersTable = ({
  userData,
  paginationModel,
  setPaginationModel,
  isLoading,
  sortModel,
  setSortModel,
  onUserSelect,
  refreshData,
  setRefreshData,
}) => {
  const theme = useTheme();
  const [selectedRows, setSelectedRows] = useState([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedUser, setSelectedUser] = useState(null);

  const handleRowSelect = (checked, userId) => {
    if (checked) {
      setSelectedRows((prev) => [...prev, userId]);
      onUserSelect(userId);
    } else {
      setSelectedRows((prev) => prev.filter((id) => id !== userId));
      onUserSelect(userId);
    }
  };

  const handleRowClick = (params, event) => {
    if (event.target.type === "checkbox") {
      return;
    }
    setSelectedUser(params.row);
    setShowCreateModal(true);
  };

  useEffect(() => {
    setSelectedRows([]);
  }, [userData, refreshData]);

  return (
    <Box
      sx={{
        height: "calc(100vh - 165px)",
        width: "100%",
        border: "1px solid",
        borderRadius: "12px",
        borderColor: "text.disabled",
        padding: "10px",
      }}
    >
      <DataGrid
        rows={userData?.results || []}
        columns={columns(
          theme.palette,
          selectedRows,
          onUserSelect,
          handleRowSelect,
        )}
        columnHeaderHeight={40}
        getRowClassName={(params) =>
          selectedRows.includes(params.id) ? "custom-selected" : ""
        }
        sx={{
          "& .custom-selected": {
            backgroundColor: "background.neutral",
          },
          "& .MuiDataGrid-row:hover": {
            cursor: "pointer",
            backgroundColor: "background.default",
          },
          "& .MuiDataGrid-columnHeaders": {
            fontSize: "13px",
            padding: "8px",
            backgroundColor: "background.paper",
            color: "text.disabled",
          },
          "& .MuiDataGrid-columnHeader": {
            borderRight: "1px solid",
            borderColor: theme.palette.divider,
          },
          "& .MuiDataGrid-cell": {
            fontSize: "12px",
            padding: "16px",
          },
          "& .MuiDataGrid-overlay": {
            backgroundColor: "background.paper",
          },
          "& .MuiDataGrid-cell:focus": {
            outline: "none", // Prevent focus outline
          },
          "& .MuiDataGrid-columnHeader:focus": {
            outline: "none", // Prevent focus outline on column headers
          },
          "& .MuiDataGrid-columnHeader:focus-visible": {
            outline: "none", // Prevent focus outline when visible on column headers
          },
        }}
        rowCount={userData?.total || 0}
        paginationModel={paginationModel}
        loading={isLoading}
        paginationMode="server"
        onPaginationModelChange={setPaginationModel}
        disableRowSelectionOnClick
        disableColumnFilter
        disableColumnMenu
        getRowHeight={() => 50}
        sortModel={sortModel}
        onSortModelChange={(newSortModel) => setSortModel(newSortModel)}
        onRowClick={handleRowClick}
      />
      <CreateUserModal
        open={showCreateModal}
        onClose={() => {
          setShowCreateModal(false);
          setSelectedUser(null);
        }}
        userData={selectedUser}
        type="editUser"
        setRefreshData={setRefreshData}
      />
    </Box>
  );
};

UsersTable.propTypes = {
  userData: PropTypes.object,
  paginationModel: PropTypes.any,
  setPaginationModel: PropTypes.any,
  isLoading: PropTypes.bool,
  sortModel: PropTypes.object,
  setSortModel: PropTypes.func,
  onUserSelect: PropTypes.func.isRequired,
  refreshData: PropTypes.bool,
  setRefreshData: PropTypes.func,
};

export default UsersTable;
