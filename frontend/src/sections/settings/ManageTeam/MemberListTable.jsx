import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";

import DeleteTeamMember from "./DeleteTeamMember";

const MemberListTable = ({ data }) => {
  const [selectedUser, setSelectedUser] = useState(null);
  const queryClient = useQueryClient();
  const { user: loggedInUser } = useAuthContext();

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      axios.delete(endpoints.settings.teams.deleteMember(selectedUser.id)),
    onSuccess: () => {
      setSelectedUser(null);
      queryClient.invalidateQueries({ queryKey: ["member-list"], type: "all" });
    },
  });

  return (
    <>
      <DeleteTeamMember
        selectedUser={selectedUser}
        onClose={() => setSelectedUser(null)}
        onDeleteClick={() => mutate()}
        isLoading={isPending}
      />
      <TableContainer>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Email</TableCell>
              <TableCell>Role</TableCell>
              <TableCell>Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.map((user) => (
              <TableRow
                key={user.id}
                sx={{
                  "&:hover": {
                    cursor: "pointer",
                    backgroundColor: "action.hover",
                  },
                }}
              >
                <TableCell>{user.email}</TableCell>
                <TableCell>{user.organization_role}</TableCell>
                <TableCell width="140px">
                  {user.id !== loggedInUser.id && (
                    <Button
                      className="row-hover-action"
                      variant="contained"
                      color="error"
                      size="small"
                      onClick={() => setSelectedUser(user)}
                    >
                      Delete User
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </>
  );
};

MemberListTable.propTypes = {
  data: PropTypes.array,
};

export default MemberListTable;
