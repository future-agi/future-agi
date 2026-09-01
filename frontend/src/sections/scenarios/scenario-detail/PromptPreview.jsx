import { Box, Button, CircularProgress, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import MarkdownWithVariableHighlight from "src/components/ReactMarkdownWithHighlight";
import SvgColor from "src/components/svg-color";
import EditPrompt from "./EditPrompt";
import { useState } from "react";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useQuery } from "@tanstack/react-query";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  isScenarioFailed,
  isScenarioInProgress,
} from "src/utils/scenarioStatus";

const PromptPreview = ({ scenario }) => {
  const { role } = useAuthContext();
  const isWriteDisabled =
    !RolePermission.SIMULATION_AGENT[PERMISSIONS.UPDATE][role];
  const { data: tableData } = useQuery(
    getDatasetQueryOptions(scenario.dataset, 0, [], [], "", { enabled: false }),
  );

  const allowedVariables = useMemo(() => {
    const columnConfig = tableData?.data?.result?.column_config ?? [];

    const allowedVariables = columnConfig.map((col) => col.name);
    return allowedVariables;
  }, [tableData?.data?.result?.column_config]);

  const hasPrompts = Boolean(scenario?.prompts?.[0]?.content);
  const isProcessing = !hasPrompts && isScenarioInProgress(scenario?.status);
  const isFailed = !hasPrompts && isScenarioFailed(scenario?.status);

  const [open, setOpen] = useState(false);

  if (isProcessing) {
    return (
      <Box
        sx={{
          flex: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 1,
          height: "100%",
        }}
      >
        <CircularProgress size={20} />
        <Typography typography="s1">
          We are processing the prompts...
        </Typography>
      </Box>
    );
  }

  if (isFailed) {
    return (
      <Box
        sx={{
          flex: 1,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          overflow: "hidden",
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography typography="s1">
          There was an error generating the prompt
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        flex: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box
        sx={{
          paddingY: 1,
          paddingX: "12px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid",
          borderColor: "divider",
          backgroundColor: "background.paper",
        }}
      >
        <Typography typography="s2_1" fontWeight="fontWeightMedium">
          Prompt
        </Typography>
        {!isWriteDisabled && (
          <Button
            size="small"
            variant="outlined"
            startIcon={<SvgColor src="/assets/icons/ic_edit_pencil.svg" />}
            onClick={() => setOpen(true)}
          >
            Edit
          </Button>
        )}
      </Box>
      <Box
        sx={{
          paddingX: 2,
          paddingY: "12px",
          flex: 1,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Box
          sx={{
            overflowY: "auto",
            flex: 1,
          }}
        >
          <Box
            sx={{ display: "flex", gap: 1, flexDirection: "column", flex: 1 }}
          >
            {/* <Typography
            key={prompt.id}
            typography="s1"
            fontWeight="fontWeightMedium"
            sx={{ textTransform: "capitalize" }}
          >
            {scenario.prompts?.[0]?.role}
          </Typography> */}
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 0.5,
                paddingX: 2,
                paddingY: "12px",
                backgroundColor: "background.default",
                fontSize: "14px",
              }}
            >
              <MarkdownWithVariableHighlight
                content={scenario.prompts?.[0]?.content}
                variables={allowedVariables}
              />
            </Box>
          </Box>
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
          }}
        >
          <SvgColor
            src="/assets/icons/ic_info.svg"
            sx={{ width: 16, height: 16, color: "blue.500", flexShrink: 0 }}
          />
          <Typography typography="s2" fontWeight="fontWeightMedium">
            Make sure your scenario table below contains all the column that are
            used as variables in the prompt
          </Typography>
        </Box>
      </Box>
      <EditPrompt
        open={open}
        onClose={() => setOpen(false)}
        prompts={scenario?.prompts || []}
        variables={allowedVariables}
      />
    </Box>
  );
};

PromptPreview.propTypes = {
  scenario: PropTypes.object,
};

export default PromptPreview;
