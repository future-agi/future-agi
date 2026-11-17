import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "react-router-dom";
import { Box } from "@mui/material";
import LeaderboardsList from "src/sections/common/Leaderboards/LeaderboardsList";
import LeaderboardBoard from "src/sections/common/Leaderboards/LeaderboardBoard";

/**
 * `/dashboard/develop/leaderboards` and `/dashboard/develop/leaderboards/:leaderboardId`
 * (W5-5, W5-SCOPE §7 C2; routes are integration C-H7): workspace leaderboards
 * that rank runs from several datasets by a saved runs rule.
 */
export default function Leaderboards() {
  const { leaderboardId } = useParams();
  return (
    <>
      <Helmet>
        <title>Leaderboards</title>
      </Helmet>
      <Box sx={{ p: 3, maxWidth: 1280 }}>
        {leaderboardId ? (
          <LeaderboardBoard id={leaderboardId} />
        ) : (
          <LeaderboardsList />
        )}
      </Box>
    </>
  );
}
