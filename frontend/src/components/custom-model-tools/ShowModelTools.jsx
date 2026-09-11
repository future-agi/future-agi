import {
  Box,
  Button,
  Drawer,
  IconButton,
  Skeleton,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Iconify from "src/components/iconify";
import { LoadingButton } from "@mui/lab";
import { FormSearchSelectFieldState } from "src/components/FromSearchSelectField";
import SvgColor from "src/components/svg-color";
import EditTool from "./EditTool";
import { useRunPromptOptions } from "src/api/develop/develop-detail";
import { ShowComponent } from "../show";

const ShowModelToolsChild = ({ onClose, handleApply, tools }) => {
  const theme = useTheme();
  const [selectedTool, setSelectedTool] = useState(
    tools?.map((item) => {
      return Object.prototype.hasOwnProperty.call(item, "tool")
        ? item?.tool?.value
        : item?.id;
    }),
  );
  const [openEdit, setOpenEdit] = useState(null);
  const [createNew, setCreateNew] = useState(false);
  const { data: runPromptOptions, isLoading } = useRunPromptOptions();
  const availableTools = runPromptOptions?.available_tools;

  // useMemo(() => {
  //   if (tools?.length > 0) {
  //     setSelectedTool(tools?.map((item) => item?.tool?.value));
  //   }
  // }, [tools]);

  const handleCreateTool = () => {
    setOpenEdit(null);
    setCreateNew(true);
  };

  const handleEditTool = (item) => {
    setOpenEdit(item);
  };

  const handleDeleteTool = (item) => {
    setSelectedTool((pre) => pre.filter((temp) => temp !== item.id));
  };

  const handleApplyTool = () => {
    const allSelectedTools = availableTools.filter((temp) =>
      selectedTool.includes(temp.id),
    );
    handleApply(allSelectedTools);
    onClose();
  };

  return (
    <Box
      sx={{
        border: "1px solid",
        borderBottom: "0px",
        borderColor: "divider",
        borderRadius: "8px 8px 0px 0px",
        padding: "16px",
        display: "grid",
        gridTemplateRows: "auto 1fr auto",
        gap: "16px",
        overflow: "auto",
        height: "100%",
      }}
    >
      <Box
        sx={{
          gap: "16px",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <Typography
            variant="m3"
            fontWeight={"fontWeightSemiBold"}
            color="text.primary"
          >
            Tools
          </Typography>
          <Typography
            variant="s1"
            fontWeight={"fontWeightRegular"}
            color="text.secondary"
          >
            Explore all actions taken and their outcomes during the tool run.
          </Typography>
        </Box>
        <IconButton
          onClick={onClose}
          sx={{
            borderRadius: "4px",
            position: "absolute",
            top: "16px",
            right: "16px",
          }}
        >
          <Iconify icon="mingcute:close-line" color="text.primary" />
        </IconButton>
      </Box>
      <Box
        sx={{
          height: "100%",
          overflowY: "auto",
          gap: 2,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Button
          onClick={handleCreateTool}
          variant="outlined"
          startIcon={<Iconify icon="eva:plus-fill" />}
          sx={{
            width: "150px",
            height: "38px",
            border: "1px solid",
            borderColor: "border.default",
            color: "text.primary",
            fontWeight: (theme) => theme.typography.fontWeightMedium,
            ...theme.typography["s1"],
            display: "flex",
            alignItems: "center",
            flexDirection: "row",
            py: (theme) => theme.spacing(1),
            px: (theme) => theme.spacing(3),
            borderRadius: "8px",
            "& .MuiButton-startIcon": {
              marginRight: "4px",
            },
            // "&:hover": {
            //   backgroundColor: "background.neutral",
            // },
          }}
        >
          Create tool
        </Button>
        <FormSearchSelectFieldState
          label={"Tool"}
          value={selectedTool}
          fullWidth
          size="small"
          placeholder="Select tool"
          onChange={(e) => setSelectedTool(e.target.value)}
          options={availableTools?.map((item) => ({
            label: item.name,
            value: item.id,
          }))}
          multiple
          checkbox
        />
        <ShowComponent condition={isLoading}>
          <Skeleton variant="rectangular" width="100%" height={32} />
          <Skeleton variant="rectangular" width="100%" height={32} />
          <Skeleton variant="rectangular" width="100%" height={32} />
        </ShowComponent>
        <ShowComponent condition={createNew}>
          <EditTool
            editTool={null}
            onCancel={() => {
              setCreateNew(false);
              setOpenEdit(null);
            }}
          />
        </ShowComponent>
        {availableTools
          ?.filter((temp) => selectedTool?.includes(temp.id))
          ?.map((item, index) => {
            return openEdit?.id === item.id ? (
              <EditTool
                key={index}
                editTool={item}
                onCancel={() => {
                  setCreateNew(false);
                  setOpenEdit(null);
                }}
              />
            ) : (
              <Box
                key={index}
                sx={{
                  padding: "8px",
                  borderRadius: "8px",
                  height: "32px",
                  backgroundColor: "background.neutral",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography
                  variant="s3"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  {item.name}
                </Typography>
                <Box sx={{ display: "flex", gap: "8px" }}>
                  <IconButton
                    onClick={() => handleEditTool(item)}
                    sx={{ padding: "4px", height: "24px", borderRadius: "4px" }}
                  >
                    <SvgColor
                      src="/assets/prompt/editPencil.svg"
                      sx={{
                        height: "16px",
                        width: "16px",
                        color: "text.primary",
                      }}
                    />
                  </IconButton>
                  <IconButton
                    onClick={() => handleDeleteTool(item)}
                    sx={{ padding: "4px", height: "24px", borderRadius: "4px" }}
                  >
                    <Iconify
                      icon="mingcute:close-line"
                      color="text.primary"
                      width={16}
                      height={16}
                    />
                  </IconButton>
                </Box>
              </Box>
            );
          })}
      </Box>
      <Box
        sx={{
          height: "max-content",
          overflowY: "auto",
          gap: 2,
          display: "flex",
          flexDirection: "row",
          justifyContent: "flex-end",
        }}
      >
        <LoadingButton
          onClick={handleApplyTool}
          variant="contained"
          color="primary"
          sx={{
            width: "200px",
            height: "38px",
            fontWeight: (theme) => theme.typography.fontWeightMedium,
            ...theme.typography["s1"],
            py: (theme) => theme.spacing(1),
            px: (theme) => theme.spacing(3),
          }}
        >
          Save
        </LoadingButton>
      </Box>
    </Box>
  );
};

ShowModelToolsChild.propTypes = {
  onClose: PropTypes.func,
  handleApply: PropTypes.func,
  tools: PropTypes.array,
};

const ShowModelTools = ({ open, onClose, handleApply, tools }) => {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "550px",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <ShowModelToolsChild
        onClose={onClose}
        handleApply={handleApply}
        tools={tools}
      />
    </Drawer>
  );
};

export default ShowModelTools;

ShowModelTools.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  handleApply: PropTypes.func,
  tools: PropTypes.array,
};
