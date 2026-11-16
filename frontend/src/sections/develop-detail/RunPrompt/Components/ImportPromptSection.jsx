import React from "react";
import { InputAdornment, Stack } from "@mui/material";
import ImportPromptBtn from "./ImportPromptBtn";
import Iconify from "src/components/iconify";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { trackEvent, Events } from "src/utils/Mixpanel";
import PropTypes from "prop-types";

export default function ImportPromptSection({
  control,
  promptHasBeenImported,
  handleRemovePrompt,
  setOpenImportPromptModal,
  children,
}) {
  const showImport = Boolean(setOpenImportPromptModal);

  if (!showImport) {
    return <>{children}</>;
  }

  return (
    <>
      <Stack
        direction={"row"}
        alignItems={"center"}
        justifyContent={"space-between"}
      >
        {children}
        <ImportPromptBtn
          promptImportred={promptHasBeenImported}
          onClick={() => {
            if (promptHasBeenImported) {
              handleRemovePrompt();
            } else {
              setOpenImportPromptModal(true);
              trackEvent(Events.datasetImportPromptClicked);
            }
          }}
        />
      </Stack>

      {promptHasBeenImported && (
        <>
          <FormTextFieldV2
            onClick={() => setOpenImportPromptModal(true)}
            control={control}
            fieldName="config.prompt"
            label="Select prompt"
            variant="outlined"
            placeholder="Select prompt"
            fullWidth
            size="small"
            sx={{
              "& .MuiOutlinedInput-root": {
                paddingRight: "8px",
                cursor: "pointer",
              },
              "& .MuiOutlinedInput-input": {
                cursor: "pointer",
              },
            }}
            InputProps={{
              readOnly: true,
              endAdornment: (
                <InputAdornment position="end">
                  <Iconify
                    icon="ci:chevron-down"
                    sx={{ color: "text.primary" }}
                  />
                </InputAdornment>
              ),
            }}
          />
          <FormTextFieldV2
            onClick={() => setOpenImportPromptModal(true)}
            control={control}
            fieldName="config.promptVersion"
            label="Version"
            variant="outlined"
            placeholder="Enter prompt version"
            fullWidth
            size="small"
            sx={{
              "& .MuiOutlinedInput-root": {
                paddingRight: "8px",
                cursor: "pointer",
              },
              "& .MuiOutlinedInput-input": {
                cursor: "pointer",
              },
            }}
            InputProps={{
              readOnly: true,
              endAdornment: (
                <InputAdornment position="end">
                  <Iconify
                    icon="ci:chevron-down"
                    sx={{ color: "text.primary" }}
                  />
                </InputAdornment>
              ),
            }}
          />
        </>
      )}
    </>
  );
}

ImportPromptSection.propTypes = {
  control: PropTypes.object,
  promptHasBeenImported: PropTypes.bool,
  handleRemovePrompt: PropTypes.func,
  setOpenImportPromptModal: PropTypes.func,
  children: PropTypes.node,
};
