import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tab, Tabs, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { TAB_LABELS } from "./iterateModel";
import AgentNodeChange from "./changes/AgentNodeChange";
import PromptChange from "./changes/PromptChange";
import ModelChange from "./changes/ModelChange";
import ToolsChange from "./changes/ToolsChange";

const BODIES = {
  agent: AgentNodeChange,
  prompt: PromptChange,
  model: ModelChange,
  tools: ToolsChange,
};

/** Column 1 · Change: only the tabs that apply to the run's kind. */
const ChangePane = ({ dock }) => {
  const { tabs, draft, patch } = dock;
  const tab = tabs.includes(draft.tab) ? draft.tab : tabs[0];
  const Body = BODIES[tab] ?? null;
  return (
    <Stack sx={{ minWidth: 0, height: "100%" }}>
      {draft.reason ? (
        // iterate r1 G3 (P1): what this fix is for, from `Fix this path`.
        <Stack
          direction="row"
          alignItems="flex-start"
          gap={0.75}
          data-testid="iterate-fix-reason"
          sx={(t) => ({
            mb: 1,
            px: 1,
            py: 0.75,
            borderRadius: 0.75,
            bgcolor: alpha(t.palette.error.main, 0.06),
            border: "1px solid",
            borderColor: alpha(t.palette.error.main, 0.24),
          })}
        >
          <Iconify
            icon="mdi:target"
            width={16}
            sx={{ mt: "2px", flexShrink: 0, color: "error.main" }}
          />
          <Typography typography="s2" sx={{ minWidth: 0 }}>
            {draft.reason}
          </Typography>
        </Stack>
      ) : null}
      <Tabs
        value={tab}
        onChange={(e, v) => patch({ tab: v })}
        variant="scrollable"
        sx={{
          minHeight: 32,
          borderBottom: "1px solid",
          borderColor: "divider",
          "& .MuiTab-root": {
            minHeight: 32,
            py: 0.5,
            px: 1,
            mr: 1.5,
            minWidth: 0,
            typography: "s2",
            fontWeight: 500,
          },
        }}
      >
        {tabs.map((t) => (
          <Tab key={t} value={t} label={TAB_LABELS[t]} />
        ))}
      </Tabs>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", pt: 1, pr: 0.5 }}>
        {Body ? <Body dock={dock} /> : null}
      </Box>
    </Stack>
  );
};

ChangePane.propTypes = { dock: PropTypes.object.isRequired };

export default ChangePane;
