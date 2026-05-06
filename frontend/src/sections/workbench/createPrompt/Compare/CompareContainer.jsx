import { Box } from "@mui/material";
import React from "react";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import VariableDrawer from "src/components/VariableDrawer/VariableDrawer";
import ImportDatasetDrawer from "src/components/VariableDrawer/ImportDataset/ImportDatasetDrawer";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import { useExtractAllVariables } from "../hooks/use-extract-all-variables";
import PromptActions from "../promptActions/PromptActions";
import CompareOutputs from "./CompareOutputs";
import CompareInputs from "./CompareInputs";
import { ShowComponent } from "src/components/show";
import Evaluation from "../Evaluation";

const CompareContainer = () => {
  const {
    prompts,
    currentTab,
    variableDrawerOpen,
    setVariableDrawerOpen,
    variableData,
    isImportDatasetDrawerOpen,
    setImportDatasetDrawerOpen,
    setVariableData,
    templateFormat,
  } = usePromptWorkbenchContext();

  const handleOpenDrawer = () => {
    setVariableDrawerOpen(false);
    setImportDatasetDrawerOpen(true);
  };

  const handleCloseDrawer = () => {
    setImportDatasetDrawerOpen(false);
    setVariableDrawerOpen(true);
  };

  const variables = useExtractAllVariables(prompts, templateFormat);

  return (
    <Box
      display="flex"
      flexDirection={"column"}
      justifyContent={"space-between"}
      gap={1}
      width="100%"
      height="100%"
      sx={{
        overflow: "hidden",
      }}
    >
      <PromptActions />
      <ShowComponent condition={currentTab === "Playground"}>
        <Box
          sx={{
            flex: 1,
            width: "100%",
            height: "100%",
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            paddingX: "16px",
            gap: 1,
          }}
        >
          <ResizablePanels
            leftPanel={<CompareInputs />}
            rightPanel={<CompareOutputs />}
            orientation="vertical"
            initialLeftWidth={50}
            minLeftWidth={30}
            maxLeftWidth={70}
          />
          <VariableDrawer
            open={variableDrawerOpen}
            onClose={() => setVariableDrawerOpen(false)}
            variableData={variableData}
            setVariableData={setVariableData}
            variables={variables}
            onOpenImportDatasetDrawer={handleOpenDrawer}
          />
          <ImportDatasetDrawer
            open={isImportDatasetDrawerOpen}
            onClose={handleCloseDrawer}
            variables={variables}
            setVariableData={setVariableData}
          />
        </Box>
      </ShowComponent>
      <ShowComponent condition={currentTab === "Evaluation"}>
        <Evaluation />
      </ShowComponent>
    </Box>
  );
};

export default CompareContainer;
