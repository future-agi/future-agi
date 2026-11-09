import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ListSubheader from "@mui/material/ListSubheader";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";
import { useHandoffAdmission } from "../lib/useHandoffAdmission";

import { handoffFlags, useThisTargets } from "../lib/useThis";

import CopyCommand, { copyText } from "./CopyCommand";
import HubIcon from "./HubIcon";
import { HubSection } from "./HubStates";

// "Use this" (SPEC §7.2, §7.4): a primary button with a menu grouped by
// target, then the install and run commands. A handoff item shows only when
// the version supports it AND the receiving tab has shipped; the CLI item
// only once a customer can install a CLI with `omega hub`. With no target the
// section is not drawn.
export default function UseThisMenu({
  entryRef,
  version,
  capabilities,
  flags,
}) {
  const [anchor, setAnchor] = useState(null);
  const navigate = useNavigate();
  const cliReleased = useOmegaPart(OMEGA_PART.HUB_CLI);
  const admission = useHandoffAdmission();
  const targets = useThisTargets({
    ref: entryRef,
    version,
    capabilities,
    flags: { ...handoffFlags(), cli: cliReleased, ...flags },
    ...admission,
  });
  const cli = targets.find((t) => t.key === "cli");
  const close = () => setAnchor(null);
  if (targets.length === 0) return null;
  return (
    <HubSection title="Use this" testId="hub-use-this">
      <Button
        variant="contained"
        color="primary"
        fullWidth
        onClick={(event) => setAnchor(event.currentTarget)}
        endIcon={<HubIcon name="chevronDown" size={14} />}
        data-testid="hub-use-this-button"
        aria-haspopup="menu"
      >
        Use this
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        slotProps={{ paper: { sx: { minWidth: 260 } } }}
        data-testid="hub-use-this-menu"
      >
        {targets.flatMap((target) => [
          <ListSubheader
            key={`${target.key}-h`}
            sx={{ lineHeight: "28px", typography: "s3" }}
          >
            {target.group}
          </ListSubheader>,
          target.kind === "copy" ? (
            <MenuItem
              key={target.key}
              data-testid={`hub-use-this-${target.key}`}
              onClick={async () => {
                await copyText(target.commands.add);
                close();
              }}
            >
              <HubIcon name="terminal" size={15} sx={{ mr: 1 }} />
              <Typography typography="s2">Copy the install command</Typography>
            </MenuItem>
          ) : (
            <MenuItem
              key={target.key}
              data-testid={`hub-use-this-${target.key}`}
              onClick={() => {
                close();
                navigate(target.href);
              }}
            >
              <HubIcon name="external" size={15} sx={{ mr: 1 }} />
              <Typography typography="s2">{target.label}</Typography>
            </MenuItem>
          ),
        ])}
      </Menu>
      {cli && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
          <CopyCommand text={cli.commands.add} testId="hub-cli-add" />
          <CopyCommand text={cli.commands.run} testId="hub-cli-run" />
        </Box>
      )}
    </HubSection>
  );
}

UseThisMenu.propTypes = {
  entryRef: PropTypes.string.isRequired,
  version: PropTypes.string,
  capabilities: PropTypes.object,
  flags: PropTypes.object,
};
