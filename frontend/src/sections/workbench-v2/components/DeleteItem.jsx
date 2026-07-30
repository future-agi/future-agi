import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { iconStyles, PROMPT_ICON_MAPPER, PROMPT_ITEM_TYPES } from "../common";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "../../../utils/axios";
import { useNavigate, useParams } from "react-router";
import _ from "lodash";
import { Events, PropertyName, trackEvent } from "../../../utils/Mixpanel";

export const DeleteItem = ({
  createdBy = null,
  id,
  open,
  onClose,
  type = PROMPT_ITEM_TYPES.FILE,
  name,
}) => {
  const queryClient = useQueryClient();
  const { folder } = useParams();
  const navigate = useNavigate();
  const { mutate, isPending: isDeleting } = useMutation({
    mutationFn: async (id) => {
      if (type === PROMPT_ITEM_TYPES.FILE) {
        const data = { ids: [id] };
        return axios.post(endpoints.develop.runPrompt.promptMultiDelete, data);
      } else if (type === PROMPT_ITEM_TYPES.TEMPLATE) {
        return axios.delete(endpoints.develop.runPrompt.promptTemplateId(id));
      } else {
        return axios.delete(endpoints.develop.runPrompt.promptFolderId(id));
      }
    },
    onSuccess: () => {
      onClose();
      if (type === PROMPT_ITEM_TYPES.FILE) {
        queryClient.invalidateQueries({
          queryKey: ["folder-items", folder],
        });
      } else if (type === PROMPT_ITEM_TYPES.TEMPLATE) {
        queryClient.invalidateQueries({
          queryKey: ["prompt-templates"],
        });
      } else {
        if (folder === id) {
          navigate("/dashboard/workbench/all");
        }
        queryClient.invalidateQueries({
          queryKey: ["prompt-folders"],
        });
        queryClient.invalidateQueries({
          queryKey: ["folder-items"],
        });
      }
    },
  });

  const handleDelete = () => {
    if (id) {
      trackEvent(Events.promptDeleteConfirmed, {
        [PropertyName.id]: id,
        [PropertyName.type]: type,
      });
      mutate(id);
    }
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={`Delete ${_.toLower(type)}`}
      actionBtnTitle="Delete"
      subTitle={`Are you sure you want to delete this ${_.toLower(type)}?`}
      dialogActionSx={{
        "& button:last-of-type": {
          backgroundColor: "error.main",
          color: "common.white",
          "&:hover": {
            backgroundColor: "error.dark",
          },
        },
      }}
      onSubmit={handleDelete}
      isLoading={isDeleting}
      isValid={Boolean(id)}
    >
      <Box
        sx={{
          borderStyle: "solid",
          borderWidth: "1px",
          borderColor: "divider",
          padding: "16px",
          borderRadius: "8px",
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: "12px",
        }}
      >
        <Box
          sx={{
            boxShadow: iconStyles.boxShadow,
            height: 44,
            width: 44,
            bgcolor: "background.paper",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Box
            component="img"
            src={PROMPT_ICON_MAPPER[type] ?? PROMPT_ICON_MAPPER["PROMPT"]}
            sx={{
              height: 20,
              width: 20,
            }}
          />
        </Box>

        <Box sx={{ flex: 1 }}>
          <Typography fontSize={"16px"} fontWeight={500}>
            {name}
          </Typography>
          {createdBy && (
            <Typography fontSize={"13px"} fontWeight={400}>
              {`Created by ${createdBy}`}
            </Typography>
          )}
        </Box>
      </Box>
    </ModalWrapper>
  );
};

DeleteItem.propTypes = {
  id: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  name: PropTypes.string,
  type: PropTypes.string,
  createdBy: PropTypes.string,
};
