import {
  Box,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import DevelopDetailProvider from "src/sections/develop-detail/DevelopDetailProvider";
import { ShowComponent } from "src/components/show";
import DevelopDataV2 from "src/sections/develop-detail/DataTab/DevelopDataV2";
import { useReplaySessionsStoreShallow } from "./store";
import SvgColor from "../../../../components/svg-color";

export const GeneratedScenarios = React.memo(({ scenarioDetail: scenario }) => {
  const { setExpandView, expandView } = useReplaySessionsStoreShallow((s) => ({
    setExpandView: s.setExpandView,
    expandView: s.expandView,
  }));
  return (
    <Stack gap={1}>
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        <Typography
          typography={"s1"}
          color={"text.primary"}
          fontWeight={"fontWeightMedium"}
        >
          {scenario?.name}
        </Typography>
        <IconButton
          size="small"
          sx={{
            color: "text.primary",
            height: 24,
            width: 24,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            mr: 1,
          }}
          onClick={() => setExpandView(!expandView)}
        >
          <SvgColor
            sx={{
              height: 16,
              width: 16,
            }}
            src={
              expandView
                ? "/assets/icons/ic_minimize.svg"
                : "/assets/icons/ic_maximize.svg"
            }
          />
        </IconButton>
      </Stack>
      <DevelopDetailProvider>
        <ShowComponent condition={!scenario?.dataset}>
          <Box
            sx={{
              height: "320px",
            }}
          >
            <ShowComponent condition={scenario?.status === "Processing"}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 2,
                  paddingX: 2,
                  height: "100%",
                  justifyContent: "center",
                }}
              >
                <CircularProgress size={20} />
                <Typography typography="s1">
                  We are generating the scenario...
                </Typography>
              </Box>
            </ShowComponent>
            <ShowComponent condition={scenario?.status === "Failed"}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 2,
                  paddingX: 2,
                  height: "100%",
                  justifyContent: "center",
                }}
              >
                <Typography typography="s1">
                  There was an error generating the scenario.
                </Typography>
              </Box>
            </ShowComponent>
          </Box>
        </ShowComponent>
        <ShowComponent condition={scenario?.dataset}>
          <Box
            sx={{
              height: "439px",
              display: "flex",
              flexDirection: "column",
              "& > .dataset-table": {
                padding: "0 !important",
              },
            }}
          >
            <DevelopDataV2
              datasetId={scenario?.dataset}
              viewOptions={{
                showDrawer: false,
                bottomRow: false,
                showCheckbox: false,
                showEvals: false,
              }}
            />
          </Box>
        </ShowComponent>
      </DevelopDetailProvider>
    </Stack>
  );
});

GeneratedScenarios.displayName = "GeneratedScenarios";
GeneratedScenarios.propTypes = {
  scenarioDetail: PropTypes.object.isRequired,
};
export default GeneratedScenarios;
