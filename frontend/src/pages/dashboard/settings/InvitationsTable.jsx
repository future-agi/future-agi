import React, { useEffect, useState } from "react";
import { DataGrid } from "@mui/x-data-grid";
import { Box, useTheme, Checkbox, Tooltip } from "@mui/material";
import PropTypes from "prop-types";
import CreateUserModal from "./CreateUserModal";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { ResendInviteIcon, DeleteInviteIcon } from "./IconComponents";
import ConfirmDeleteInvitations from "./ConfirmDeleteInvitations";
import DeleteConfirmationDialog from "./DeleteConfirmationDialog";
import CircularProgress from "@mui/material/CircularProgress"; // Import loader component
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import logger from "src/utils/logger";
import { fDateLocal } from "src/utils/format-time";
import { LocalDateTime } from "src/components/date-time/LocalDateTime";

const handleResend = async (invitationId, email, setLoadingResend) => {
  trackEvent(Events.resendInviteClicked, {
    [PropertyName.email]: email,
  });
  setLoadingResend((prev) => ({ ...prev, [invitationId]: true })); // Set loading for this ID
  try {
    const response = await axios.post(endpoints.stripe.resendInvitationEmails, {
      user_ids: [invitationId],
    });

    if (response.status === 200) {
      enqueueSnackbar("Invitation resent successfully", { variant: "success" });
    } else {
      enqueueSnackbar("Failed to resend invitation", { variant: "error" });
    }
  } catch (error) {
    logger.error("Error resending invitation:", error);
  } finally {
    setLoadingResend((prev) => ({ ...prev, [invitationId]: false })); // Reset loading
  }
};

const handleDelete = async (invitationId, setRefreshData, closeDialog) => {
  trackEvent(Events.deleteInviteClicked, {
    [PropertyName.email]: invitationId,
  });
  try {
    const response = await axios.delete(endpoints.stripe.deleteUsers, {
      data: { user_ids: [invitationId] },
    });

    if (response.status === 200) {
      enqueueSnackbar("Invitation deleted successfully", {
        variant: "success",
      });
    } else {
      enqueueSnackbar("Failed to delete invitation", { variant: "error" });
    }
  } catch (error) {
    logger.error("Error deleting invitation:", error);
  } finally {
    setRefreshData((prev) => !prev);
    closeDialog(); // Close the confirmation dialog
  }
};

const columns = (
  palette,
  selectedRows,
  onInviteSelect,
  handleRowSelect,
  setRefreshData,
  setOpenConfirmDeleteInvitations,
  setSelectedDeleteId,
  setOpenDeleteConfirmationDialog,
  loadingResend,
  setLoadingResend,
) => [
  {
    field: "checkbox",
    headerName: "",
    width: 50,
    sortable: false,
    renderCell: (params) => (
      <Checkbox
        checked={selectedRows.includes(params.row.id)}
        onChange={(event) => {
          handleRowSelect(event.target.checked, params.row.id);
        }}
      />
    ),
  },
  { field: "name", headerName: "User Name", flex: 1, sortable: false },
  { field: "email", headerName: "Email", flex: 1, sortable: false },
  {
    field: "created_at",
    headerName: "Date Invited",
    flex: 1,
    sortable: false,
    valueFormatter: (params) => fDateLocal(params.value),
    renderCell: (params) => (
      <LocalDateTime value={params.value} emptyText="No invite date recorded" />
    ),
  },
  {
    field: "action",
    headerName: "Action",
    flex: 1,
    sortable: false,
    renderCell: (params) => (
      <Box sx={{ display: "flex", justifyContent: "space-around" }}>
        <Tooltip title="Resend Invite" arrow>
          {/* <img
            src="/public/icons/settings/settings_resend_invite.png"
            alt="Resend Invite"
            style={{
              width: '20px',
              height: '20px',
              marginRight: '5px',
            }}
            onClick={() => {handleResend(params.row.id)}}
            // onMouseEnter={() => {console.log("resend hovered")}}
          /> */}
          <span
            onClick={() =>
              !loadingResend[params.row.id]
                ? handleResend(
                    params.row.id,
                    params.row.email,
                    setLoadingResend,
                  )
                : {}
            }
            style={{
              // cursor: 'pointer',
              cursor: loadingResend[params.row.id] ? "not-allowed" : "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              marginRight: "10px",
            }}
          >
            {loadingResend[params.row.id] ? (
              <CircularProgress size={20} />
            ) : (
              <ResendInviteIcon
                width={20}
                height={20}
                fill="#A792FD"
                stroke="#A792FD"
                strokeWidth={0.0}
              />
            )}{" "}
          </span>
        </Tooltip>
        <Tooltip title="Delete Invite" arrow>
          {/* <img
            src="/public/icons/settings/settings_delete_invite.png"
            alt="Delete Invite"
            style={{
              width: '20px',
              height: '20px',
              marginRight: '5px',
            }}
            onClick={() => {handleDelete(params.row.id, setRefreshData)}}
            // onMouseEnter={() => {console.log("delete hovered")}}
          /> */}
          <span
            onClick={() => {
              setOpenDeleteConfirmationDialog(true);
              setSelectedDeleteId(params.row.id);
            }}
            style={{
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <DeleteInviteIcon
              width={20}
              height={20}
              fill="none"
              stroke="#DB2F2D"
              strokeWidth={1.0}
            />
          </span>
        </Tooltip>
      </Box>
    ),
    cellClassName: "no-hover-effect",
  },
];

const InvitationsTable = ({
  userData,
  paginationModel,
  setPaginationModel,
  isLoading,
  sortModel,
  setSortModel,
  onInviteSelect,
  setRefreshData,
}) => {
  const theme = useTheme();
  const [selectedRows, setSelectedRows] = useState([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedUser, setSelectedUser] = useState(null);
  const [openConfirmDeleteInvitations, setOpenConfirmDeleteInvitations] =
    useState(false);
  const [openDeleteConfirmationDialog, setOpenDeleteConfirmationDialog] =
    useState(false);
  const [selectedDeleteId, setSelectedDeleteId] = useState(null);
  const [loadingResend, setLoadingResend] = useState({});

  const handleRowSelect = (checked, userId) => {
    if (checked) {
      setSelectedRows([...selectedRows, userId]);
      onInviteSelect(userId);
    } else {
      setSelectedRows(selectedRows.filter((id) => id !== userId));
      onInviteSelect(userId);
    }
  };

  const handleRowClick = (params, event) => {
    // console.log("event12 : ", event);
    // console.log("event.target : ", event.target);
    // console.log("event.target.type : ", event.target?.type);
    // console.log("event.target.tagName : ", event.target?.tagName);
    // ignore if target is <img>
    if (
      event.target.type === "checkbox" ||
      event.target.type === "button" ||
      event.target.tagName === "IMG" ||
      event.target.tagName === "path" ||
      event.target.tagName === "svg"
    ) {
      return;
    }
    setSelectedUser(params.row);
    setShowCreateModal(true);
  };

  useEffect(() => {
    const fetchUserData = async () => {
      // Your API call to fetch user data
      // Example: const response = await axios.get(endpoints.stripe.getUserData);
      // setUserData(response.data);
    };

    fetchUserData();
  }, []);

  return (
    <Box
      sx={{
        height: "calc(100vh - 165px)",
        width: "100%",
        border: "1px solid",
        borderRadius: "15px",
        borderColor: "text.disabled",
        padding: "10px",
      }}
    >
      <DataGrid
        rows={userData?.results || []}
        columns={columns(
          theme.palette,
          selectedRows,
          onInviteSelect,
          handleRowSelect,
          setRefreshData,
          null,
          setSelectedDeleteId,
          setOpenDeleteConfirmationDialog,
          loadingResend,
          setLoadingResend,
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
            borderRight: "1px solid", // Add right border to each header cell
            borderColor: theme.palette.divider, // Use theme color for the border
          },
          "& .MuiDataGrid-cell": {
            fontSize: "12px",
            padding: "16px",
          },
          "& .MuiDataGrid-cell.Mui-selected": {
            outline: "none", // Ensure no outline for selected cells
            backgroundColor: "transparent", // Optional: Keep background transparent for selected cells
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
        onRowClick={(params, event) => {
          if (params.field !== "action") {
            handleRowClick(params, event);
          }
        }}
      />
      <CreateUserModal
        open={showCreateModal}
        onClose={() => {
          setShowCreateModal(false);
          setSelectedUser(null);
        }}
        userData={selectedUser}
        type="editInvitation"
        setRefreshData={setRefreshData}
      />
      <ConfirmDeleteInvitations
        open={openConfirmDeleteInvitations}
        onClose={() => {
          setOpenConfirmDeleteInvitations(false);
        }}
        onConfirm={() => {}}
        isLoading={false}
        count={selectedRows.length}
      />
      <DeleteConfirmationDialog
        open={openDeleteConfirmationDialog}
        onClose={() => setOpenDeleteConfirmationDialog(false)}
        invitationId={selectedDeleteId}
        setRefreshData={setRefreshData}
        handleDelete={handleDelete}
        title="Confirm  Delete"
        message="Are you sure you want to delete this invitation?"
      />
    </Box>
  );
};

InvitationsTable.propTypes = {
  userData: PropTypes.object,
  paginationModel: PropTypes.any,
  setPaginationModel: PropTypes.any,
  isLoading: PropTypes.bool,
  sortModel: PropTypes.object,
  setSortModel: PropTypes.func,
  onInviteSelect: PropTypes.func.isRequired,
  setRefreshData: PropTypes.func,
};

export default InvitationsTable;
