import {
  Box,
  Button,
  CircularProgress,
  InputAdornment,
  LinearProgress,
  Tabs,
  Tab,
} from "@mui/material";
import React, { useState } from "react";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import UsersTable from "./UsersTable"; // Assuming you have a UsersTable component
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import UsersNoData from "./UsersNoData"; // Assuming you have a UsersNoData component
import CreateUserModal from "./CreateUserModal"; // Assuming you have a CreateUserModal component
import { Events, trackEvent, PropertyName } from "src/utils/Mixpanel";
import InvitationsTable from "./InvitationsTable";
import { enqueueSnackbar } from "notistack";
import ConfirmDeleteUsers from "./ConfirmDeleteUsers"; // Import the confirmation modal
import ConfirmDeleteInvitations from "src/pages/dashboard/settings/ConfirmDeleteInvitations"; // Import the confirmation modal
import { useDebounce } from "src/hooks/use-debounce";
import FormSearchField from "src/components/FormSearchField/FormSearchField";

const UserManagement = () => {
  const [refreshDataInvites, setRefreshDataInvites] = useState(false);
  const [refreshDataUsers, setRefreshDataUsers] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 300);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [paginationModel, setPaginationModel] = useState({
    page: 0,
    pageSize: 10,
  });
  const [sortModel, setSortModel] = useState([
    {
      field: "user_name", // Adjust based on your data structure
    },
  ]);
  const [tabValue, setTabValue] = useState(0); // State for tab selection
  const [selectedUsers, setSelectedUsers] = useState([]); // State for selected users
  const [selectedInvites, setSelectedInvites] = useState([]); // State for selected invites
  const [openDeleteConfirm, setOpenDeleteConfirm] = useState(false); // State for modal visibility
  const [isLoadingDelete, setIsLoadingDelete] = useState(false); // State for loading indicator
  const [openDeleteInvitesConfirm, setOpenDeleteInvitesConfirm] =
    useState(false); // State for modal visibility
  const [isLoadingInvitesDelete, setIsLoadingInvitesDelete] = useState(false); // State for loading indicator

  const { isLoading: isLoadingUsers, data: usersData } = useQuery({
    queryKey: [
      "users",
      paginationModel.page,
      sortModel?.[0]?.sort,
      debouncedSearchQuery,
      tabValue,
      refreshDataUsers,
    ],
    queryFn: async () => {
      const response = await axios.get(endpoints.settings.teams.getMemberList, {
        params: {
          page_size: paginationModel.pageSize,
          page: paginationModel.page + 1,
          sort_order: sortModel?.[0]?.sort,
          search_query: searchQuery,
          is_active: true,
        },
      });
      return response;
    },
    select: (d) => d?.data?.result,
  });

  const { isLoading: isLoadingInvites, data: invitesData } = useQuery({
    queryKey: [
      "owner-org-invites",
      paginationModel.page,
      sortModel?.[0]?.sort,
      debouncedSearchQuery,
      tabValue,
      refreshDataInvites,
    ],
    queryFn: async () => {
      const response = await axios.get(endpoints.settings.teams.getMemberList, {
        params: {
          page: paginationModel.page + 1,
          sort_order: sortModel?.[0]?.sort,
          search_query: searchQuery,
          is_active: false,
        },
      });
      return response;
    },
    select: (d) => d?.data?.result,
  });

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue); // Update the selected tab
    setSelectedUsers([]); // Reset selected users when switching tabs
    setSelectedInvites([]); // Reset selected invites when switching tabs
    if (newValue === 1) {
      trackEvent(Events.invitationApprovalClicked);
    }
  };

  // Function to handle user selection
  const handleUserSelection = (userId) => {
    // console.log("userId : ", userId);
    // console.log("selectedUsers : ", selectedUsers.length);
    // console.log("selectedUsers : ", selectedUsers);
    setSelectedUsers((prevSelected) => {
      if (prevSelected.includes(userId)) {
        return prevSelected.filter((id) => id !== userId); // Deselect user
      } else {
        return [...prevSelected, userId]; // Select user
      }
    });
  };

  // Function to handle delete action
  const handleDeleteUsers = async () => {
    trackEvent(Events.deleteUserClicked);
    setOpenDeleteConfirm(true); // Show confirmation modal
  };

  const handleConfirmDelete = async () => {
    trackEvent(Events.confirmedDeleteClicked, {
      [PropertyName.formFields]: {
        userid: selectedUsers,
      },
    });
    setIsLoadingDelete(true); // Set loading state
    // console.log("Deleting users:", selectedUsers);
    try {
      const response = await axios.delete(endpoints.stripe.deleteUsers, {
        data: {
          user_ids: selectedUsers,
        },
      });

      // console.log("response : ", response);
      if (response.status === 200) {
        enqueueSnackbar("Users deleted successfully", { variant: "success" });
      } else {
        enqueueSnackbar("Failed to delete users", { variant: "error" });
      }
    } catch (error) {
      if (error?.error) {
        enqueueSnackbar("Failed to delete user : " + error?.error, {
          variant: "error",
        });
      } else {
        enqueueSnackbar("Failed to delete user", { variant: "error" });
      }

      // console.error("Error deleting users:", error);
    } finally {
      setRefreshDataUsers((prev) => !prev); // Refresh data
      setIsLoadingDelete(false); // Reset loading state
      setSelectedUsers([]); // Reset selected users after deletion
      setOpenDeleteConfirm(false); // Close confirmation modal
    }
  };

  const handleInviteSelection = (inviteId) => {
    // console.log("inviteId : ", inviteId);
    // console.log("selectedInvites : ", selectedInvites.length);
    setSelectedInvites((prevSelected) => {
      if (prevSelected.includes(inviteId)) {
        // console.log("removing invite");
        return prevSelected.filter((id) => id !== inviteId); // Deselect invite
      } else {
        // console.log("adding invite");
        return [...prevSelected, inviteId]; // Select invite
      }
    });
  };

  const handleDeleteInvites = async () => {
    setOpenDeleteInvitesConfirm(true); // Show confirmation modal
  };

  const handleConfirmDeleteInvites = async () => {
    setIsLoadingInvitesDelete(true); // Set loading state
    // console.log("Deleting invites:", selectedInvites);
    try {
      const response = await axios.delete(endpoints.stripe.deleteUsers, {
        data: {
          user_ids: selectedInvites,
        },
      });
      // console.log("response : ", response);
      if (response.status === 200) {
        enqueueSnackbar("Invitations deleted successfully", {
          variant: "success",
        });
      } else {
        enqueueSnackbar("Failed to delete invitations", { variant: "error" });
      }
    } catch (error) {
      // console.error("Error deleting invitations:", error);
    } finally {
      setRefreshDataInvites((prev) => !prev); // Refresh data
      setIsLoadingInvitesDelete(false); // Reset loading state
      setSelectedInvites([]); // Reset selected invites after deletion
      setOpenDeleteInvitesConfirm(false); // Close confirmation modal
    }
  };

  return (
    <>
      <Helmet>
        <title>User Management</title>
      </Helmet>

      <Tabs value={tabValue} onChange={handleTabChange}>
        <Tab label="User Management" />
        <Tab label="Invitation Approval" />
      </Tabs>

      <Box sx={{ paddingX: "2px" }}>
        {/* Search and Add/Delete User for User Management Tab */}
        {tabValue === 0 && (
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
            <FormSearchField
              autoFocus
              searchQuery={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              size="small"
              // onFocus={() => trackEvent(Events.searchUserClicked)}
              sx={{ width: 400 }} // Set the width to 100px
              placeholder="Search"
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="solar:magnifier-linear"
                      width={16}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
                endAdornment: isLoadingUsers ? (
                  <InputAdornment position="end">
                    <CircularProgress size={20} color="primary" />
                  </InputAdornment>
                ) : (
                  <></>
                ),
              }}
            />
            {selectedUsers.length > 0 ? ( // Conditionally render buttons based on selection
              <Box sx={{ display: "flex", gap: 2 }}>
                <Button
                  variant="contained"
                  color="secondary"
                  onClick={handleDeleteUsers}
                >
                  Delete {selectedUsers.length} Users
                </Button>
                <Button
                  variant="outlined"
                  onClick={() => {
                    setSelectedUsers([]);
                    setRefreshDataUsers((prev) => !prev);
                  }} // Clear selection
                >
                  Cancel
                </Button>
              </Box>
            ) : (
              <Button
                id="add-user-button"
                variant="contained"
                color="primary"
                sx={{
                  marginLeft: 2,
                  typography: "s1",
                  px: (theme) => theme.spacing(3),
                }} // Add margin to the left of the button
                onClick={() => {
                  trackEvent(Events.addUserClicked);
                  setShowCreateModal(true);
                }}
              >
                Add User
              </Button>
            )}
          </Box>
        )}

        {/* Render content based on the selected tab */}
        {tabValue === 0 && !isLoadingUsers && (
          <>
            {isLoadingUsers && <LinearProgress />}
            {!isLoadingUsers && !usersData?.results?.length && <UsersNoData />}
            {!isLoadingUsers && Boolean(usersData?.results?.length) && (
              <UsersTable
                userData={usersData}
                isLoading={isLoadingUsers}
                paginationModel={paginationModel}
                setPaginationModel={setPaginationModel}
                sortModel={sortModel}
                setSortModel={setSortModel}
                onUserSelect={handleUserSelection}
                refreshData={refreshDataUsers}
                setRefreshData={setRefreshDataUsers}
              />
            )}
          </>
        )}
        {tabValue === 1 && !isLoadingInvites && (
          <>
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
              <FormSearchField
                searchQuery={searchQuery}
                autoFocus
                onChange={(e) => setSearchQuery(e.target.value)}
                size="small"
                sx={{ width: 400 }} // Set the width to 100px
                placeholder="Search"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="solar:magnifier-linear"
                        width={16}
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment:
                    isLoadingUsers || isLoadingInvites ? (
                      <InputAdornment position="end">
                        <CircularProgress size={20} color="primary" />
                      </InputAdornment>
                    ) : (
                      <></>
                    ),
                }}
              />
              {selectedInvites.length > 0 ? ( // Conditionally render buttons based on selection
                <Box sx={{ display: "flex", gap: 2 }}>
                  <Button
                    variant="contained"
                    color="secondary"
                    onClick={handleDeleteInvites}
                  >
                    Delete {selectedInvites.length} Invites
                  </Button>
                  <Button
                    variant="outlined"
                    onClick={() => {
                      setSelectedInvites([]);
                      setRefreshDataInvites((prev) => !prev);
                    }} // Clear selection
                  >
                    Cancel
                  </Button>
                </Box>
              ) : (
                <Button
                  id="add-invite-button"
                  variant="contained"
                  color="primary"
                  sx={{ marginLeft: 2, width: "130px" }} // Add margin to the left of the button
                  onClick={() => {
                    trackEvent(Events.addInviteClicked);
                    setShowCreateModal(true);
                  }}
                >
                  Add Invite
                </Button>
              )}
            </Box>
            {isLoadingInvites && <LinearProgress />}
            {!isLoadingInvites && !invitesData?.results?.length && (
              <UsersNoData />
            )}
            {!isLoadingInvites && Boolean(invitesData?.results?.length) && (
              <InvitationsTable
                userData={invitesData} // Copying the same content for now
                isLoading={isLoadingInvites}
                paginationModel={paginationModel}
                setPaginationModel={setPaginationModel}
                sortModel={sortModel}
                setSortModel={setSortModel}
                onInviteSelect={handleInviteSelection} // Pass selection handler to InvitationsTable
                setRefreshData={setRefreshDataInvites}
              />
            )}
          </>
        )}
      </Box>
      <CreateUserModal
        open={showCreateModal}
        onClose={() => {
          setShowCreateModal(false);
        }}
        type="create"
        setRefreshData={setRefreshDataInvites}
      />
      <ConfirmDeleteUsers
        open={openDeleteConfirm}
        onClose={() => {
          // trackEvent(Events.cancelDeleteClicked); // Track event when closing
          setOpenDeleteConfirm(false);
        }}
        onConfirm={handleConfirmDelete}
        isLoading={isLoadingDelete}
        count={selectedUsers.length}
      />
      <ConfirmDeleteInvitations
        open={openDeleteInvitesConfirm}
        onClose={() => setOpenDeleteInvitesConfirm(false)}
        onConfirm={handleConfirmDeleteInvites}
        isLoading={isLoadingInvitesDelete}
        count={selectedInvites.length} // Pass the count of selected invites
      />
    </>
  );
};

export default UserManagement;
