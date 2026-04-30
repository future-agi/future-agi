import { Box, useTheme } from "@mui/material";
import React from "react";
import ResizablePanels from "src/components/resizablePanels/ResizablePanels";
import ImportDatasetDrawer from "src/components/VariableDrawer/ImportDataset/ImportDatasetDrawer";
import VariableDrawer from "src/components/VariableDrawer/VariableDrawer";

import PromptSection from "./Playground/PromptSection";
import OutputSection from "./Playground/OutputSection/OutputSection";
import { usePromptWorkbenchContext } from "./WorkbenchContext";
import { useExtractAllVariables } from "./hooks/use-extract-all-variables";
import SavePromptTemplate from "../../workbench-v2/components/SavePromptTemplate";
import { usePromptStoreShallow } from "../../workbench-v2/store/usePromptStore";
import { ChoosePromptTemplateDrawer } from "../ChoosePromptTemplateDrawer";

const Playground = () => {
  const theme = useTheme();
  const {
    promptGeneratingStatus,
    results,
    loadingPrompt,
    prompts,
    placeholders,
    setPromptsByIndex,
    modelConfig,
    setModelConfigByIndex,
    variableDrawerOpen,
    setVariableDrawerOpen,
    variableData,
    isImportDatasetDrawerOpen,
    setImportDatasetDrawerOpen,
    setVariableData,
    setPlaceholdersByIndex,
    templateFormat,
  } = usePromptWorkbenchContext();

  const {
    selectTemplateDrawerOpen,
    setSelectTemplateDrawerOpen,
    openSaveTemplate,
    setOpenSaveTemplate,
  } = usePromptStoreShallow((state) => ({
    selectTemplateDrawerOpen: state.selectTemplateDrawerOpen,
    setSelectTemplateDrawerOpen: state.setSelectTemplateDrawerOpen,
    openSaveTemplate: state.openSaveTemplate,
    setOpenSaveTemplate: state.setOpenSaveTemplate,
  }));

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
      justifyContent={"space-between"}
      gap={theme.spacing(1)}
      width="100%"
      height="100%"
      sx={{
        overflow: "hidden",
        paddingX: 2,
      }}
    >
      <ResizablePanels
        leftPanel={
          <Box sx={{ paddingRight: 1 }}>
            <PromptSection
              prompts={prompts[0]?.prompts || []}
              setPrompts={(v) => setPromptsByIndex(0, v)}
              modelConfig={modelConfig?.[0] || {}}
              setModelConfig={(v, options) =>
                setModelConfigByIndex(0, v, options)
              }
              index={0}
              placeholders={placeholders[0] || []}
              setPlaceholders={(v) => setPlaceholdersByIndex(0, v)}
            />
          </Box>
        }
        rightPanel={
          <Box sx={{ paddingLeft: 1, height: "100%" }}>
            <OutputSection
              results={results[0]}
              loadingPrompt={loadingPrompt}
              promptGeneratingStatus={promptGeneratingStatus[0]}
              responseFormat={modelConfig?.[0]?.responseFormat}
              outputFormat={modelConfig?.[0]?.output_format}
            />
          </Box>
        }
        initialLeftWidth={65}
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
      <SavePromptTemplate
        data={openSaveTemplate}
        open={Boolean(openSaveTemplate)}
        onClose={() => setOpenSaveTemplate(false)}
      />
      <ChoosePromptTemplateDrawer
        open={selectTemplateDrawerOpen}
        onClose={() => setSelectTemplateDrawerOpen(false)}
        importMode
      />
    </Box>
  );
};

export default Playground;
