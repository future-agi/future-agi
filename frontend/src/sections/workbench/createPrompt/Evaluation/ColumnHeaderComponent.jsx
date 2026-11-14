import { Box, Button, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { useWorkbenchEvaluationContext } from "./context/WorkbenchEvaluationContext";
import HeaderPromptRenderer from "./HeaderPromptRenderer";
import { ShowComponent } from "src/components/show";
import { OriginTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { COLUMNIDS } from "./common";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const CustomWorkbenchEvaluationColumn = (props) => {
  const { displayName, col, columnGroup, headerLevel } = props;
  const { role } = useAuthContext();
  const { setCompareOpen, setVersions, versions } =
    useWorkbenchEvaluationContext();
  const theme = useTheme();
  const iconStyle = {
    color: "text.disabled",
  };

  const renderIcon = () => {
    if (col.originType === OriginTypes.EVALUATION) {
      return (
        <Iconify
          icon="solar:check-circle-linear"
          sx={{ color: "status.success.fg", flexShrink: 0 }}
        />
      );
    }
    return <Iconify icon="solar:notes-linear" sx={iconStyle} />;
  };

  if (columnGroup) {
    const isRunPrompt = col?.originType === OriginTypes.RUN_PROMPT;
    const isEval = col?.originType === OriginTypes.EVALUATION;
    if (headerLevel === 1 && isRunPrompt) {
      const { messages } = col;
      return <HeaderPromptRenderer messages={messages ?? []} />;
    } else if (headerLevel === 2 && isRunPrompt) {
      return (
        <Box
          display={"flex"}
          gap={theme.spacing(1)}
          alignItems={"center"}
          lineHeight={"1px"}
        >
          <Typography
            fontWeight={"fontWeightBold"}
            typography={"s1"}
            color={"text.disabled"}
          >
            Model
          </Typography>
          {col?.model_detail ? (
            <Box
              sx={{
                "& button": {
                  height: (theme) => theme.spacing(3),
                },
              }}
            >
              <CustomModelDropdown
                openSelectModel={false}
                setOpenSelectModel={() => {}}
                hoverPlacement="bottom-start"
                buttonTitle="Select Model"
                buttonIcon={
                  <Iconify
                    icon="solar:graph-linear"
                    width="16px"
                    height="16px"
                    sx={{
                      // cursor: "pointer",
                      color: "text.primary",
                    }}
                  />
                }
                value={col.model_detail.model_name}
                modelDetail={col.model_detail}
                disabledClick
                searchDropdown={undefined}
                onChange={undefined}
                onModelConfigOpen={undefined}
                inputSx={undefined}
              />
            </Box>
          ) : (
            <Box
              bgcolor={"background.paper"}
              flexDirection={"row"}
              display={"flex"}
              alignItems={"center"}
              border={"1px solid"}
              borderRadius={theme.spacing(0.25)}
              borderColor={"divider"}
              gap={theme.spacing(0.5)}
              px={theme.spacing(1)}
              py={theme.spacing(0.5)}
            >
              <Typography typography={"s3"} fontWeight={"fontWeightMedium"}>
                {col?.model_detail?.model_name}
              </Typography>
            </Box>
          )}
        </Box>
      );
    } else if (headerLevel === 2 && isEval) {
      return (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            flexDirection: "row",
            gap: 1,
            width: "100%",
            justifyContent: "center",
          }}
        >
          {renderIcon()}
          <Typography
            fontWeight={"fontWeightMedium"}
            typography={"s1"}
            color={"text.primary"}
          >
            {displayName}
          </Typography>
        </Box>
      );
    }
    return <></>;
  }

  if (col.id === COLUMNIDS.COMPARISON) {
    return (
      <Button
        variant="text"
        color="primary"
        disabled={!RolePermission.PROMPTS[PERMISSIONS.UPDATE][role]}
        onClick={() => {
          setCompareOpen(true);
        }}
        startIcon={
          <SvgColor width={16}
            src="/assets/icons/action_buttons/ic_add.svg"
            color="primary.main"
            sx={{ }}
          />
        }
        fullWidth
        sx={{
          justifyContent: "flex-start",
        }}
      >
        <Typography typography={"s1"} fontWeight={"600"}>
          Add Comparison
        </Typography>
      </Button>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        height: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          flexDirection: "row",
          gap: 1,
        }}
      >
        <ShowComponent condition={col.originType !== OriginTypes.EVALUATION}>
          {renderIcon()}
          <Typography
            fontWeight={"fontWeightBold"}
            typography={"s1"}
            color={"text.disabled"}
          >
            {displayName}
          </Typography>
        </ShowComponent>
        {col.template_version && (
          <Box
            bgcolor={"action.hover"}
            px={"6px"}
            py={"3px"}
            borderRadius={theme.spacing(0.25)}
          >
            <Typography typography={"s3"} fontWeight={"fontWeightMedium"}>
              {col.template_version}
            </Typography>
          </Box>
        )}
      </Box>
      <ShowComponent
        condition={versions.length > 1 && displayName?.startsWith("Output")}
      >
        <IconButton
          onClick={() =>
            setVersions((pre) =>
              pre.filter((version) => version !== col.template_version),
            )
          }
        >
          <SvgColor width={16}
            src="/assets/icons/ic_delete.svg"
            color="text.primary"
            sx={{ }}
          />
        </IconButton>
      </ShowComponent>
    </Box>
  );
};

export default CustomWorkbenchEvaluationColumn;

CustomWorkbenchEvaluationColumn.propTypes = {
  displayName: PropTypes.string,
  col: PropTypes.any,
  columnGroup: PropTypes.any,
  headerLevel: PropTypes.number,
};
