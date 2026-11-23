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
import OriginChip from "../../components/OriginChip";
import SectionCard from "../../components/SectionCard";
import ViewerLock from "../../components/ViewerLock";
import {
  CONTRACT_LISTS_COPY as L,
  MONO_FONT,
  RULE_DETAIL_COPY,
} from "./contract.constants";

const detailFor = (details, text) => {
  const matches = (details || []).filter((detail) => detail?.text === text);
  return matches.length === 1 ? matches[0] : null;
};

function ItemText({ text, detail }) {
  const meta = [
    detail?.enforced_by?.length
      ? RULE_DETAIL_COPY.enforcedBy(detail.enforced_by)
      : null,
    detail?.sub_goal ? RULE_DETAIL_COPY.checkedBy(detail.sub_goal) : null,
  ].filter(Boolean);
  return (
    <Box sx={{ minWidth: 0 }}>
      {detail?.short && (
        <Typography
          sx={{
            typography: "s1",
            fontWeight: 500,
            fontFamily: MONO_FONT,
            overflowWrap: "anywhere",
          }}
        >
          {detail.short}
        </Typography>
      )}
      <Typography
        sx={{
          typography: detail?.short ? "s3" : "s1",
          color: detail?.short ? "text.secondary" : "text.primary",
        }}
      >
        {text}
      </Typography>
      {meta.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}>
          {meta.join(" · ")}
        </Typography>
      )}
    </Box>
  );
}

ItemText.propTypes = {
  text: PropTypes.string.isRequired,
  detail: PropTypes.shape({
    short: PropTypes.string,
    enforced_by: PropTypes.arrayOf(PropTypes.string),
    sub_goal: PropTypes.string,
  }),
};

export default function ContractListCard({
  list,
  items,
  details,
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
          {items.map((text, index) => {
            const detail = detailFor(details, text);
            const origin = detail?.origin;
            return (
              <Box
                key={`${index}:${text}`}
                sx={{
                  display: "grid",
                  gridTemplateColumns: [
                    "40px",
                    "minmax(0, 1fr)",
                    origin ? "auto" : null,
                    showActions ? "auto" : null,
                  ]
                    .filter(Boolean)
                    .join(" "),
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
                <ItemText text={text} detail={detail} />
                {origin && (
                  <OriginChip
                    origin={origin.kind}
                    file={origin.path || origin.field}
                    line={origin.line}
                  />
                )}
                {showActions && (
                  <RowMenu
                    label={L.rowActions(list.noun, index + 1)}
                    blockedReason={blockedReason}
                    onEdit={() => onEdit(index, text)}
                    onRemove={() => onRemove(index, text)}
                  />
                )}
              </Box>
            );
          })}
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
  details: PropTypes.arrayOf(
    PropTypes.shape({
      text: PropTypes.string,
      short: PropTypes.string,
      origin: PropTypes.object,
      enforced_by: PropTypes.arrayOf(PropTypes.string),
      sub_goal: PropTypes.string,
    }),
  ),
  showActions: PropTypes.bool,
  blockedReason: PropTypes.string,
  onAdd: PropTypes.func,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  sx: PropTypes.object,
};
