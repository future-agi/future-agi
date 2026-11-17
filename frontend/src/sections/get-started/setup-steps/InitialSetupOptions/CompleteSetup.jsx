import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import { countedSteps, listedSteps, menuesConstant } from "../../constant";
import { useInviteAccess } from "../../useInviteAccess";

// The summary after the last step counts the same steps as the step list,
// the ones that count for this reader: complete only when every one of them
// is done, otherwise how many are done.
const CompleteSetup = ({ data }) => {
  const { menusList, keySelector } = menuesConstant;
  const { countsInvite } = useInviteAccess();
  const counted = countedSteps(listedSteps(menusList), countsInvite);
  const isDone = (item) => Boolean(data?.[keySelector[item.label]]);
  const doneCount = counted.filter(isDone).length;
  const allDone = doneCount === counted.length;

  return (
    <Box
      sx={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {allDone && (
        <Iconify
          sx={{ color: "status.success.fg" }}
          icon="nrk:media-completed"
          height={40}
          width={40}
        />
      )}
      <Typography variant="title_sm" color="text.primary">
        {allDone
          ? "Setup is complete"
          : `${doneCount} of ${counted.length} steps done`}
      </Typography>
      <Typography variant="body_md" color="text.secondary">
        {allDone
          ? "Open any module from the menu on the left."
          : "Open a step on the left to finish it."}
      </Typography>
    </Box>
  );
};

export default CompleteSetup;

CompleteSetup.propTypes = {
  data: PropTypes.object,
};
