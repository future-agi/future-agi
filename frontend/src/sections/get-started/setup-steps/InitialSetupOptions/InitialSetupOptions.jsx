import { Box, Typography } from "@mui/material";
import React, { useMemo } from "react";
import BottomAction from "./BottomAction";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import { menuesConstant } from "../../constant";
import CreateFirstDataset from "./CreateFirstDataset";
import CreateFirstEvaluation from "./CreateFirstEvaluation";
import KeySection from "./KeySection";
import CompleteSetup from "./CompleteSetup";
import RunFirstExperiment from "./RunFirstExperiment";
import SetupObsabilityInApplication from "./SetupObsabilityInApplication";
import InviteTeamMembers from "./InviteTeamMembers";
import { ShowComponent } from "src/components/show";

const componentFilter = {
  addKeys: {
    bottomAction: true,
    componentKey: "key",
  },
  createFirstDataset: {
    bottomAction: false,
    componentKey: "dataset",
  },
  createFirstEvaluation: {
    bottomAction: false,
    componentKey: "evals",
  },
  RunFirstExperiment: {
    bottomAction: true,
    componentKey: "experiment",
  },
  SetupObsabilityInApplication: {
    bottomAction: true,
    componentKey: "observe",
  },
  inviteTeamMembers: {
    bottomAction: true,
    componentKey: "invite",
  },
  complete: { bottomAction: false, componentKey: "complete" },
};

const InitialSetupOptions = ({ currentLabel, setCurrentLabel, data }) => {
  const { bottomAction, componentKey } = useMemo(() => {
    if (Object.prototype.hasOwnProperty.call(componentFilter, currentLabel)) {
      return componentFilter[currentLabel];
    }
    return { bottomAction: false, componentKey: "complete" };
  }, [currentLabel]);
  // The step list highlights the open step instead of showing its check
  // mark, so a finished step says it is done here.
  const stepDone =
    componentKey !== "complete" &&
    Boolean(data?.[menuesConstant.keySelector[currentLabel]]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        backgroundColor: `background.paper`,
        width: "calc(100% - 290px)",
        // Scroll, never hide, whatever does not fit, such as a step's button.
        overflowX: "hidden",
        overflowY: "auto",
      }}
    >
      <Box
        sx={{
          flex: 1,
          // Without the bottom bar, a step's own button gets the same space
          // below it as the step has on top.
          padding: bottomAction ? "16px 16px 0px" : "16px",
          width: "100%",
        }}
      >
        <ShowComponent condition={stepDone}>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              mb: "12px",
              color: "status.success.fg",
            }}
          >
            <SvgColor
              aria-hidden
              width={16}
              src="/assets/icons/navbar/ic_evaluate.svg"
            />
            <Typography
              variant="s2"
              fontWeight={"fontWeightMedium"}
              color="inherit"
            >
              This step is done.
            </Typography>
          </Box>
        </ShowComponent>
        <ShowComponent condition={componentKey === "key"}>
          <KeySection setCurrentLabel={setCurrentLabel} />
        </ShowComponent>
        <ShowComponent condition={componentKey === "dataset"}>
          <CreateFirstDataset setCurrentLabel={setCurrentLabel} />
        </ShowComponent>
        <ShowComponent condition={componentKey === "evals"}>
          <CreateFirstEvaluation setCurrentLabel={setCurrentLabel} />
        </ShowComponent>
        <ShowComponent condition={componentKey === "experiment"}>
          <RunFirstExperiment setCurrentLabel={setCurrentLabel} />
        </ShowComponent>
        <ShowComponent condition={componentKey === "observe"}>
          <SetupObsabilityInApplication setCurrentLabel={setCurrentLabel} />
        </ShowComponent>
        <ShowComponent condition={componentKey === "invite"}>
          <InviteTeamMembers />
        </ShowComponent>
        <ShowComponent condition={componentKey === "complete"}>
          <CompleteSetup data={data} />
        </ShowComponent>
      </Box>
      <ShowComponent condition={bottomAction}>
        <BottomAction
          currentLabel={currentLabel}
          setCurrentLabel={setCurrentLabel}
        />
      </ShowComponent>
    </Box>
  );
};

InitialSetupOptions.propTypes = {
  currentLabel: PropTypes.string,
  setCurrentLabel: PropTypes.func,
  data: PropTypes.object,
};

export default InitialSetupOptions;
