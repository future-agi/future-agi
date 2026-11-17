import {
  Box,
  Button,
  Drawer,
  IconButton,
  Skeleton,
  Typography,
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
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

/**
 * `Look up an order by id. · order_id (string, required)` — a tool's
 * description and a peek at its parameters (IT-R1 G4). Pure.
 */
export function toolPeek(tool) {
  const fn = tool?.config?.function ?? tool?.function ?? {};
  const desc = tool?.description ?? fn.description ?? "";
  const props = fn.parameters?.properties ?? {};
  const required = new Set(fn.parameters?.required ?? []);
  const params = Object.entries(props)
    .map(
      ([k, v]) =>
        `${k} (${v?.type ?? "any"}${required.has(k) ? ", required" : ""})`,
    )
    .join(", ");
  return [desc, params].filter(Boolean).join(" · ");
}

/** A picker row: the tool's name, then its description and parameters. */
function ToolOptionPeek({ tool }) {
  const peek = toolPeek(tool);
  return (
    <Box sx={{ minWidth: 0, py: 0.25 }}>
      <Typography
        typography="s1"
        color="text.primary"
        noWrap
        sx={{ fontFamily: "ui-monospace, SFMono-Regular, monospace" }}
      >
        {tool?.name}
      </Typography>
      {peek ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ whiteSpace: "normal", wordBreak: "break-word" }}
        >
          {peek}
        </Typography>
      ) : null}
    </Box>
  );
}
ToolOptionPeek.propTypes = { tool: PropTypes.object };

const ShowModelToolsChild = ({ onClose, handleApply, tools }) => {
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
            Pick tools this prompt can call.
          </Typography>
        </Box>
        <IconButton
          onClick={onClose}
          sx={{
            position: "absolute",
            top: "16px",
            right: "16px",
          }}
        >
          <Iconify icon="solar:close-linear" color="text.primary" />
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
          startIcon={<Iconify icon="solar:add-linear" />}
          sx={{ alignSelf: "flex-start" }}
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
            // IT-R2 G4 P3: what the tool does and takes, before you pick it.
            component: <ToolOptionPeek tool={item} />,
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
                  minHeight: "32px",
                  backgroundColor: "background.neutral",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                {/* IT-R1 G4: what the tool does and what it takes. */}
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    variant="s3"
                    fontWeight={"fontWeightMedium"}
                    color="text.primary"
                  >
                    {item.name}
                  </Typography>
                  {toolPeek(item) ? (
                    <Typography
                      variant="s3"
                      color="text.secondary"
                      component="div"
                      sx={{ wordBreak: "break-word" }}
                    >
                      {toolPeek(item)}
                    </Typography>
                  ) : null}
                </Box>
                <Box sx={{ display: "flex", gap: "8px" }}>
                  <IconButton size="small" onClick={() => handleEditTool(item)}>
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
                    size="small"
                    onClick={() => handleDeleteTool(item)}
                  >
                    <Iconify
                      icon="solar:close-linear"
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
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "480px",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
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
