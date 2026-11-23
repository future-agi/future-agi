import PropTypes from "prop-types";
import { Button, Stack } from "@mui/material";
import ConnectSection from "./ConnectSection";
import { SectionHead } from "./starts/startBits";
import { MCP_ITEM } from "./starts/startItems";

export default function DrawerCatalog({ onPick, onBrowseWorlds }) {
  return (
    <Stack spacing={4} sx={{ pb: 4 }}>
      <SectionHead
        title="Use a prebuilt world"
        subtitle="Choose a released environment and customize it with your requirements and files."
        action={<Button onClick={onBrowseWorlds}>Browse templates</Button>}
      />
      <ConnectSection onPick={onPick} onPickMcp={() => onPick(MCP_ITEM)} />
    </Stack>
  );
}

DrawerCatalog.propTypes = {
  onPick: PropTypes.func.isRequired,
  onBrowseWorlds: PropTypes.func.isRequired,
};
