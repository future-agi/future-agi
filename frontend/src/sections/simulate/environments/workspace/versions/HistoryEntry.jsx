import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  ButtonBase,
  Collapse,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { relativeTime } from "src/utils/format-time";
import { canMakeDefault } from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import { PreflightBadge } from "../agents/PreflightStatus";
import { named } from "../agents/agentVersionStatus";
import MakeDefaultConfirm from "./MakeDefaultConfirm";
import VersionDetail from "./VersionDetail";
import { historyEntryId } from "./historyTimeline";

const KIND_ICON = {
  environment: "solar:layers-minimalistic-linear",
  suite: "solar:checklist-minimalistic-linear",
  agent: "solar:cpu-bolt-linear",
};
const IN_USE = new Set(["inUse", "tested"]);

// One version in the History timeline. The row opens what changed in it; its
// actions menu makes it the default.
export default function HistoryEntry({
  entry,
  envId,
  move,
  siblings,
  defaultId,
  expanded,
  onToggle,
  onOpen,
}) {
  const { piece, version, badges, originLine } = entry;
  const [menu, setMenu] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { history, menu: MENU } = VERSIONS_COPY;
  const title = `${VERSIONS_COPY.pieces[piece]} ${named(version)}`;
  const meta = [
    originLine,
    version.createdBy && VERSIONS_COPY.by(version.createdBy),
    version.createdAt && relativeTime(version.createdAt),
  ]
    .filter(Boolean)
    .join(" · ");
  const movable = canMakeDefault(piece, version);

  const confirm = () =>
    move.mutate(
      { piece, versionId: version.id, expectedDefaultId: defaultId },
      { onSettled: () => setConfirming(false) },
    );

  return (
    <Box
      id={historyEntryId(entry.key)}
      data-testid={historyEntryId(entry.key)}
      sx={{ borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="flex-start" sx={{ pr: 1.5 }}>
        <ButtonBase
          onClick={onToggle}
          aria-expanded={expanded}
          sx={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "flex-start",
            textAlign: "left",
            gap: 1.5,
            px: 2.5,
            py: 1.25,
            "&:hover": { bgcolor: "action.hover" },
            "&.Mui-focusVisible": {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: -2,
            },
          }}
        >
          <Box
            sx={{
              width: 28,
              height: 28,
              borderRadius: "6px",
              border: "1px solid",
              borderColor: "divider",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
              color: "text.secondary",
            }}
          >
            <Iconify icon={KIND_ICON[piece]} width={15} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Stack
              direction="row"
              spacing={0.75}
              alignItems="center"
              flexWrap="wrap"
              rowGap={0.25}
            >
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {title}
              </Typography>
              {badges.map((badge) => (
                <Typography
                  key={badge}
                  sx={{
                    typography: "s3",
                    color: IN_USE.has(badge)
                      ? "status.discovery.fg"
                      : "text.subtitle",
                  }}
                >
                  {`· ${history.badges[badge]}`}
                </Typography>
              ))}
              {piece === "agent" && (
                <PreflightBadge status={version.preflightStatus} />
              )}
            </Stack>
            {version.summary && (
              <Typography
                sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
              >
                {version.summary}
              </Typography>
            )}
            {meta && (
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  mt: 0.25,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {meta}
              </Typography>
            )}
          </Box>
        </ButtonBase>
        <IconButton
          size="small"
          aria-label={`${MENU.label}, ${title}`}
          aria-haspopup="menu"
          onClick={(e) => setMenu(e.currentTarget)}
          sx={{ mt: 1.25 }}
        >
          <Iconify icon="solar:menu-dots-bold" width={16} />
        </IconButton>
      </Stack>
      <Menu
        anchorEl={menu}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {movable && (
          <MenuItem
            sx={{ typography: "s2" }}
            onClick={() => {
              setMenu(null);
              // Opened, so a refusal shows under this version's changes.
              onOpen();
              setConfirming(true);
            }}
          >
            {VERSIONS_COPY.makeDefault.action}
          </MenuItem>
        )}
        <MenuItem
          sx={{ typography: "s2" }}
          onClick={() => {
            setMenu(null);
            onToggle();
          }}
        >
          {expanded ? MENU.hideChanges : MENU.seeChanges}
        </MenuItem>
      </Menu>
      <MakeDefaultConfirm
        piece={piece}
        version={confirming ? version : null}
        pending={move.isPending}
        onConfirm={confirm}
        onClose={() => setConfirming(false)}
      />
      {/* Slides open and shut; mounted only while open, so its changes load
          when the row is first opened. */}
      <Collapse in={expanded} unmountOnExit timeout={reduceMotion ? 0 : "auto"}>
        {/* Full width, not indented under the title: the changes hold tables. */}
        <Box sx={{ px: 2.5, pb: 2 }}>
          <VersionDetail
            envId={envId}
            move={move}
            piece={piece}
            version={version}
            siblings={siblings}
          />
        </Box>
      </Collapse>
    </Box>
  );
}

HistoryEntry.propTypes = {
  entry: PropTypes.shape({
    key: PropTypes.string.isRequired,
    piece: PropTypes.string.isRequired,
    version: PropTypes.object.isRequired,
    badges: PropTypes.arrayOf(PropTypes.string).isRequired,
    originLine: PropTypes.string,
  }).isRequired,
  envId: PropTypes.string.isRequired,
  move: PropTypes.object.isRequired,
  siblings: PropTypes.arrayOf(PropTypes.object).isRequired,
  defaultId: PropTypes.string,
  expanded: PropTypes.bool,
  // Opens or closes this row.
  onToggle: PropTypes.func.isRequired,
  // Opens this row, leaving it open if it already is.
  onOpen: PropTypes.func.isRequired,
};
