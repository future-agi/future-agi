import { Box, Button, Card, LinearProgress } from "@mui/material";
import React, { useState } from "react";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";

import MemberListTable from "./MemberListTable";
import NoMember from "./NoMember";
import InviteUserDrawer from "./InviteUserDrawer";

const ManageTeam = () => {
  const { data, isPending } = useQuery({
    queryFn: () => axios.get(endpoints.settings.teams.getMemberList),
    queryKey: ["member-list"],
    select: (d) => d.data?.result?.results ?? [],
  });

  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  return (
    <Box sx={{ paddingX: "20px" }}>
      <Card sx={{ height: "calc(100vh - 90px)" }}>
        <InviteUserDrawer
          open={isDrawerOpen}
          onClose={() => setIsDrawerOpen(false)}
        />
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            width: "100%",
            padding: 2,
          }}
        >
          <Button
            variant="contained"
            color="primary"
            size="medium"
            id="invite-member-button"
            onClick={() => setIsDrawerOpen(true)}
          >
            Invite User
          </Button>
        </Box>
        {isPending ? <LinearProgress /> : null}
        {!isPending && !data?.length && <NoMember />}
        {!isPending && Boolean(data?.length) && <MemberListTable data={data} />}
      </Card>
    </Box>
  );
};

export default ManageTeam;
