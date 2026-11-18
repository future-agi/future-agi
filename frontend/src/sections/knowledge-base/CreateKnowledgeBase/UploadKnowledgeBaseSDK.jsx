import { Box, Tab } from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { FormCodeEditor } from "src/components/form-code-editor";
import { ShowComponent } from "src/components/show";

const editorOptions = {
  selectOnLineNumbers: true,
  roundedSelection: false,
  readOnly: false,
  cursorStyle: "line",
  automaticLayout: true,
  minimap: {
    enabled: false,
  },
  wordWrap: "on",
};

const tabOptions = [
  { label: "Python", value: "python", disabled: false },
  // { label: "Typescript", value: "typescript", disabled: false },
  // { label: "Curl", value: "curl", disabled: false },
];

const UploadKnowledgeBaseSDK = ({ pythonCode }) => {
  const defaultValues = {
    name: "",
    Pcode: pythonCode,
    Tcode: "",
    CUcode: "",
  };

  const [currentTab, setCurrentTab] = useState("python");
  const { control, reset } = useForm({
    defaultValues: defaultValues,
  });

  useEffect(() => {
    reset({
      name: "",
      Pcode: pythonCode,
      Tcode: "",
      CUcode: "",
    });
  }, [pythonCode, reset]);

  return (
    <Box>
      <Box sx={{ mb: 1.5 }}>
        <SegmentedTabs
          value={currentTab}
          onChange={(e, value) => setCurrentTab(value)}
        >
          {tabOptions.map((tab) => (
            <Tab
              key={tab.value}
              label={tab.label}
              value={tab.value}
              disabled={tab.disabled}
            />
          ))}
        </SegmentedTabs>
      </Box>
      <Box
        bgcolor="background.neutral"
        borderRadius="8px"
        border="1px solid var(--border-default)"
        p={2}
      >
        <ShowComponent condition={currentTab === "python"}>
          <FormCodeEditor
            helperText={""}
            readOnly
            height="200px"
            defaultLanguage="python"
            control={control}
            fieldName="Pcode"
            options={editorOptions}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "typescript"}>
          <FormCodeEditor
            helperText={""}
            readOnly
            height="200px"
            defaultLanguage="python"
            control={control}
            fieldName="Tcode"
            options={editorOptions}
          />
        </ShowComponent>
        <ShowComponent condition={currentTab === "curl"}>
          <FormCodeEditor
            helperText={""}
            readOnly
            height="200px"
            defaultLanguage="python"
            control={control}
            fieldName="CUcode"
            options={editorOptions}
          />
        </ShowComponent>
      </Box>
    </Box>
  );
};

UploadKnowledgeBaseSDK.propTypes = {
  pythonCode: PropTypes.string,
};

export default UploadKnowledgeBaseSDK;
