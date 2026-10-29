import React from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Box, MenuItem, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import { CONNECT_AGENT_PATH } from "../agentsTabs";
import { useCreateGraph } from "src/api/agent-playground/agent-playground";
import { useAgentPlaygroundStoreShallow } from "../store";

const OPTIONS = [
  {
    id: "playground",
    title: "Playground",
    description:
      "Build and run multi-step AI agent workflows on a drag-and-drop canvas",
    iconSrc: "/assets/icons/navbar/ic_agents.svg",
  },
  {
    id: "connect",
    title: "Connect agents",
    description:
      "Connect an agent that already runs, to test it in simulations",
    iconSrc: "/assets/icons/settings/Integrations_new.svg",
  },
];

/**
 * "Create Agents" — build one in the Playground, or connect one that already
 * exists. The Playground limit only gates the Playground option; connecting
 * an agent is always available.
 */
export default function CreateAgentsButton({ canCreatePlayground = true }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const popover = usePopover();

  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));

  const { mutate: createGraph, isPending } = useCreateGraph({
    navigate,
    setCurrentAgent,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graphs"],
      });
    },
  });

  const handleSelect = (id) => {
    popover.onClose();
    if (id === "playground") createGraph();
    else navigate(CONNECT_AGENT_PATH);
  };

  return (
    <>
      <LoadingButton
        variant="contained"
        color="primary"
        size="small"
        loading={isPending}
        onClick={popover.onOpen}
        startIcon={
          <SvgColor
            src="/assets/icons/ic_add.svg"
            sx={{ height: 20, width: 20 }}
          />
        }
        endIcon={<Iconify icon="eva:chevron-down-fill" width={18} />}
      >
        Create Agents
      </LoadingButton>

      <CustomPopover
        open={popover.open}
        onClose={popover.onClose}
        arrow="top-right"
        sx={{ width: 340, p: 0.5 }}
      >
        {OPTIONS.map((option) => (
          <MenuItem
            key={option.id}
            disabled={option.id === "playground" && !canCreatePlayground}
            onClick={() => handleSelect(option.id)}
            sx={{
              alignItems: "flex-start",
              gap: 1.5,
              p: 1.25,
              borderRadius: 1,
              whiteSpace: "normal",
            }}
          >
            <SvgColor
              src={option.iconSrc}
              sx={{
                width: 20,
                height: 20,
                mt: 0.25,
                color: "text.secondary",
                flexShrink: 0,
              }}
            />
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                {option.title}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {option.description}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </CustomPopover>
    </>
  );
}

CreateAgentsButton.propTypes = { canCreatePlayground: PropTypes.bool };
