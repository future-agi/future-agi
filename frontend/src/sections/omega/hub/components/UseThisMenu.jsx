import React, { useId, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ListSubheader from "@mui/material/ListSubheader";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";
import { useHandoffAdmission } from "../lib/useHandoffAdmission";

import {
  cliCommands,
  handoffFlags,
  liveModels,
  useThisTargets,
} from "../lib/useThis";

import CopyCommand, { copyText } from "./CopyCommand";
import HubIcon from "./HubIcon";
import HubPanel from "./HubPanel";

// "Use this" (SPEC §7.2, §7.4), the first card of the entry's side column.
// For a version that names live models, once a customer can install a CLI
// with `omega hub`: a model select whose choice swaps the key to export and
// the `--live` model of the run command together, with the call cap a live
// run keeps to. For a version a shipped handoff can open (Studio, Deploy,
// the Omega Console): a menu grouped by target, named by the button that
// opens it. The add and run commands themselves sit in the entry's title
// block. With neither, no card.
export default function UseThisMenu({
  entryRef,
  version,
  capabilities,
  flags,
  models,
}) {
  const [anchor, setAnchor] = useState(null);
  const [chosen, setChosen] = useState("");
  const buttonId = useId();
  const menuId = useId();
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
  const handoffs = targets.some((t) => t.kind !== "copy");
  const live = cli && version ? liveModels(models) : [];
  const model = live.find((row) => row.model === chosen) || live[0];
  const close = () => setAnchor(null);
  if (!handoffs && !model) return null;
  const maxCalls = Number(models?.maxCalls);
  const liveRun = model
    ? [
        ...model.keys.map((key) => `export ${key}=...`),
        cliCommands(entryRef, version, undefined, model.model).run,
      ].join("\n")
    : null;
  return (
    <HubPanel title="Use this" testId="hub-use-this" dense>
      {handoffs && (
        <>
          <Button
            id={buttonId}
            variant="outlined"
            color="inherit"
            fullWidth
            onClick={(event) => setAnchor(event.currentTarget)}
            endIcon={<HubIcon name="chevronDown" size={14} />}
            data-testid="hub-use-this-button"
            aria-haspopup="menu"
            aria-controls={anchor ? menuId : undefined}
            aria-expanded={anchor ? "true" : undefined}
            sx={{ borderColor: "divider", justifyContent: "space-between" }}
          >
            Open in Future AGI
          </Button>
          <Menu
            anchorEl={anchor}
            open={Boolean(anchor)}
            onClose={close}
            MenuListProps={{ id: menuId, "aria-labelledby": buttonId }}
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
                  <Typography typography="s2">
                    Copy the install command
                  </Typography>
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
                  <Typography typography="s2">{target.label}</Typography>
                </MenuItem>
              ),
            ])}
          </Menu>
        </>
      )}
      {model && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <TextField
            select
            size="small"
            fullWidth
            label="On your own model"
            value={model.model}
            onChange={(event) => setChosen(event.target.value)}
            inputProps={{ "data-testid": "hub-use-this-model" }}
            sx={{ "& .MuiSelect-select": { typography: "code_md" } }}
          >
            {live.map((row) => (
              <MenuItem
                key={row.model}
                value={row.model}
                sx={{ typography: "code_md" }}
              >
                {row.model}
              </MenuItem>
            ))}
          </TextField>
          <CopyCommand
            text={liveRun}
            label="Copy the live run command"
            testId="hub-cli-live"
          />
          {maxCalls > 0 && (
            <Typography typography="s2" color="text.secondary">
              {`A live run makes at most ${maxCalls} model call${maxCalls === 1 ? "" : "s"}, billed by the model vendor to your key.`}
            </Typography>
          )}
        </Box>
      )}
    </HubPanel>
  );
}

UseThisMenu.propTypes = {
  entryRef: PropTypes.string.isRequired,
  version: PropTypes.string,
  capabilities: PropTypes.object,
  flags: PropTypes.object,
  models: PropTypes.object,
};
