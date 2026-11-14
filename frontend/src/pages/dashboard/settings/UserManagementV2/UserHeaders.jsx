import React from "react";
import { Box, InputAdornment } from "@mui/material";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import FormStaticDropdown from "src/components/FormStaticDropdown";
import { LEVELS } from "./constant";

const statusOptions = [
  { label: "All status", value: "" },
  { label: "Active", value: "Active" },
  { label: "Pending", value: "Pending" },
  { label: "Expired", value: "Expired" },
  { label: "Deactivated", value: "Deactivated" },
];

const roleFilterOptions = [
  { label: "All Roles", value: "" },
  { label: "Owner", value: `org_${LEVELS.OWNER}` },
  { label: "Admin", value: `org_${LEVELS.ADMIN}` },
  { label: "Member", value: `org_${LEVELS.MEMBER}` },
  { label: "Viewer", value: `org_${LEVELS.VIEWER}` },
  { label: "Workspace Admin", value: `ws_${LEVELS.WORKSPACE_ADMIN}` },
  { label: "Workspace Member", value: `ws_${LEVELS.WORKSPACE_MEMBER}` },
  { label: "Workspace Viewer", value: `ws_${LEVELS.WORKSPACE_VIEWER}` },
];

const UserHeaders = (props) => {
  const {
    searchQuery,
    setSearchQuery,
    selectedStatus,
    setSelectedStatus,
    selectedRole,
    setSelectedRole,
  } = props;

  return (
    <Box display={"flex"} gap={2}>
      <FormSearchField
        autoFocus
        searchQuery={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        size="small"
        sx={{ width: 400 }}
        placeholder="Search by name or email"
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="solar:magnifier-linear"
                width={16}
                sx={{ color: "text.secondary" }}
              />
            </InputAdornment>
          ),
        }}
      />
      <FormStaticDropdown
        options={statusOptions}
        value={selectedStatus}
        onChange={setSelectedStatus}
        sx={{ width: "200px" }}
        size="small"
      />
      <FormStaticDropdown
        options={roleFilterOptions}
        value={selectedRole}
        onChange={setSelectedRole}
        sx={{ width: "200px" }}
        size="small"
      />
    </Box>
  );
};

export default UserHeaders;

UserHeaders.propTypes = {
  searchQuery: PropTypes.string,
  setSearchQuery: PropTypes.func,
  selectedStatus: PropTypes.string,
  setSelectedStatus: PropTypes.func,
  selectedRole: PropTypes.string,
  setSelectedRole: PropTypes.func,
};
