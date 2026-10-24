import React from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Box, MenuItem, Typography } from "@mui/material";
import { LoadingButton } from "@mui/lab";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import CustomPopover, { usePopover } from "src/components/custom-popover";
import { paths } from "src/routes/paths";
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
    title: "Connect or build an agent",
    description:
      "From code (GitHub or an upload), a third-party platform, or built in Future AGI — to test and improve it",
    iconSrc: "/assets/icons/settings/Integrations_new.svg",
  },
];

/**
 * "New agent" (Agents → New agent) — build one in the Playground, or connect
 * one from code or a platform, or build one in Future AGI to test and improve. The Playground limit only gates the Playground option; connecting
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
    else navigate(`${paths.dashboard.agents}/connect`);
  };

  return (
    <>
      <LoadingButton
        variant="contained"
        color="primary"
        size="small"
        loading={isPending}
        onClick={popover.onOpen}
        startIcon={<SvgColor src="/assets/icons/ic_add.svg" width={20} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={20} />}
      >
        New agent
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
