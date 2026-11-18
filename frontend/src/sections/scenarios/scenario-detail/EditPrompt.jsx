import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogTitle,
  IconButton,
} from "@mui/material";
import React, { useRef, useState } from "react";
import { DialogContent } from "@mui/material";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { LoadingButton } from "@mui/lab";
import PromptEditor from "../../../components/PromptCards/PromptEditor";

const EditPromptChild = ({ prompts, onClose, variables }) => {
  const { scenarioId } = useParams();
  const quillRef = useRef();
  const [promptData, setPromptData] = useState([
    {
      type: "text",
      text: prompts?.find((prompt) => prompt?.role === "system")?.content || "",
    },
  ]);

  const queryClient = useQueryClient();
  const cursorPosition = useRef(0);

  const onSelectionChange = (range) => {
    if (!range) return;
    cursorPosition.current = range.index;
  };

  const { mutate: updateScenarioMutate, isPending: saveLoading } = useMutation({
    mutationFn: (data) => axios.put(endpoints.scenarios.edit(scenarioId), data),
    onSuccess: () => {
      enqueueSnackbar("Scenario updated successfully", {
        variant: "success",
      });
      onClose();
      queryClient.invalidateQueries({
        queryKey: ["scenario-detail", scenarioId],
      });
    },
  });

  const onSubmit = (e) => {
    e.preventDefault();
    if (promptData?.[0]?.text) {
      //@ts-ignore
      updateScenarioMutate({
        prompt: promptData[0].text,
      });
    }
  };

  return (
    <form onSubmit={onSubmit}>
      <DialogTitle sx={{ padding: 2 }}>
        {" "}
        Edit Prompt
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ padding: 2, overflowY: "unset", mb: 2 }}>
        <Box
          component={"div"}
          sx={{
            gap: "16px",
            display: "flex",
            flexDirection: "column",
            "& .prompt-editor-card .ql-container": {
              height: "200px",
            },
          }}
        >
          <PromptEditor
            appliedVariableData={variables.reduce((acc, key) => {
              acc[key] = ["1"];
              return acc;
            }, {})}
            prompt={[
              {
                type: "text",
                text:
                  prompts?.find((prompt) => prompt.role === "system")
                    ?.content || "",
              },
            ]}
            onPromptChange={(content) => setPromptData(content)}
            openVariableEditor={() => {}}
            ref={quillRef}
            onSelectionChange={onSelectionChange}
            setSelectedImage={() => {}}
            dropdownOptions={variables?.map((item) => ({ value: item }))}
            showEditEmbed={false}
            mentionEnabled={true}
            allowVariables
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ padding: 2 }}>
        <Button type="button" variant="outlined" size="small" onClick={onClose}>
          Cancel
        </Button>
        <LoadingButton
          type="submit"
          variant="contained"
          color="primary"
          size="small"
          loading={saveLoading}
        >
          Save
        </LoadingButton>
      </DialogActions>
    </form>
  );
};

EditPromptChild.propTypes = {
  prompts: PropTypes.array,
  onClose: PropTypes.func,
  variables: PropTypes.array,
};

const EditPrompt = ({ open, onClose, prompts, variables }) => {
  return (
    <Dialog
      sx={{
        zIndex: 1100,
      }}
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      PaperProps={{
        sx: {
          minWidth: "560px",
          maxWidth: "560px",
          minHeight: "300px",
          overflow: "visible",
        },
      }}
    >
      <EditPromptChild
        prompts={prompts}
        onClose={onClose}
        variables={variables}
      />
    </Dialog>
  );
};

EditPrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  prompts: PropTypes.string,
  variables: PropTypes.string,
};

export default EditPrompt;
