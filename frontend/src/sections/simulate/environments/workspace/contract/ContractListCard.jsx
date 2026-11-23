import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import ViewerLock from "../../components/ViewerLock";
import { CONTRACT_LISTS_COPY as L, MONO_FONT } from "./contract.constants";

export default function ContractListCard({
  list,
  items,
  showActions = false,
  blockedReason = null,
  onAdd,
  onEdit,
  onRemove,
  sx,
}) {
  const blocked = Boolean(blockedReason);
  return (
    <SectionCard
      title={list.title(items.length)}
      subtitle={list.subtitle}
      sx={sx}
      action={
        showActions ? (
          <ViewerLock locked={blocked} title={blockedReason || list.add}>
            <Button
              variant="outlined"
              size="small"
              disabled={blocked}
              onClick={onAdd}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
            >
              {list.add}
            </Button>
          </ViewerLock>
        ) : null
      }
    >
      {items.length === 0 ? (
        <Box sx={{ px: 1.5, py: 1.25 }}>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {list.empty}
          </Typography>
        </Box>
      ) : (
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {items.map((text, index) => (
            <Box
              key={`${index}:${text}`}
              sx={{
                display: "grid",
                gridTemplateColumns: showActions ? "40px 1fr auto" : "40px 1fr",
                columnGap: 1.5,
                alignItems: "center",
                px: 1.5,
                py: 1,
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO_FONT,
                  fontWeight: 500,
                  color: "text.tertiary",
                }}
              >
                {list.id(index + 1)}
              </Typography>
              <Typography sx={{ typography: "s1", minWidth: 0 }}>
                {text}
              </Typography>
              {showActions && (
                <RowMenu
                  label={L.rowActions(list.noun, index + 1)}
                  blockedReason={blockedReason}
                  onEdit={() => onEdit(index, text)}
                  onRemove={() => onRemove(index, text)}
                />
              )}
            </Box>
          ))}
        </Stack>
      )}
    </SectionCard>
  );
}

function RowMenu({ label, blockedReason, onEdit, onRemove }) {
  const [anchor, setAnchor] = useState(null);
  const blocked = Boolean(blockedReason);
  const pick = (fn) => () => {
    setAnchor(null);
    fn();
  };
  return (
    <>
      <ViewerLock locked={blocked} title={blockedReason || label}>
        <IconButton
          size="small"
          aria-label={label}
          disabled={blocked}
          onClick={(e) => setAnchor(e.currentTarget)}
        >
          <Iconify icon="solar:menu-dots-bold" width={16} />
        </IconButton>
      </ViewerLock>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem onClick={pick(onEdit)}>
          <ListItemIcon>
            <Iconify icon="solar:pen-linear" width={16} />
          </ListItemIcon>
          {L.edit}
        </MenuItem>
        <MenuItem onClick={pick(onRemove)}>
          <ListItemIcon>
            <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
          </ListItemIcon>
          {L.remove}
        </MenuItem>
      </Menu>
    </>
  );
}

RowMenu.propTypes = {
  label: PropTypes.string.isRequired,
  blockedReason: PropTypes.string,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

ContractListCard.propTypes = {
  list: PropTypes.shape({
    title: PropTypes.func.isRequired,
    subtitle: PropTypes.string,
    empty: PropTypes.string,
    add: PropTypes.string,
    id: PropTypes.func.isRequired,
    noun: PropTypes.string,
  }).isRequired,
  items: PropTypes.arrayOf(PropTypes.string).isRequired,
  showActions: PropTypes.bool,
  blockedReason: PropTypes.string,
  onAdd: PropTypes.func,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  sx: PropTypes.object,
};
