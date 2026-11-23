import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionCard from "../../components/SectionCard";
import ViewerLock from "../../components/ViewerLock";
import { CONTRACT_LISTS_COPY as L, MONO_FONT } from "./contract.constants";

function Coverage({ coverage, onGo }) {
  if (!coverage) return <span />;
  const tested = coverage.subGoals > 0;
  return (
    <ButtonBase
      onClick={() => onGo?.("scenarios")}
      sx={{
        borderRadius: "4px",
        px: 0.5,
        mx: -0.5,
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      <Typography
        noWrap
        sx={{
          typography: "s3",
          color: tested ? "text.secondary" : "text.disabled",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {tested
          ? L.coverage(coverage.subGoals, coverage.scenarios)
          : L.notTested}
      </Typography>
    </ButtonBase>
  );
}

Coverage.propTypes = {
  coverage: PropTypes.shape({
    subGoals: PropTypes.number,
    scenarios: PropTypes.number,
  }),
  onGo: PropTypes.func,
};

function RowActions({ label, blockedReason, onEdit, onRemove }) {
  const blocked = Boolean(blockedReason);
  const action = (icon, title, onClick) => (
    <ViewerLock locked={blocked} title={blockedReason || title}>
      <Tooltip arrow title={blocked ? "" : title}>
        <span>
          <IconButton
            size="small"
            aria-label={`${title} ${label}`}
            disabled={blocked}
            onClick={onClick}
          >
            <Iconify icon={icon} width={16} />
          </IconButton>
        </span>
      </Tooltip>
    </ViewerLock>
  );
  return (
    <Stack
      direction="row"
      spacing={0.25}
      className="row-edit"
      sx={{ opacity: 0.6, transition: "opacity 120ms", alignSelf: "center" }}
    >
      {action("solar:pen-linear", L.edit, onEdit)}
      {action("solar:trash-bin-minimalistic-linear", L.remove, onRemove)}
    </Stack>
  );
}

RowActions.propTypes = {
  label: PropTypes.string.isRequired,
  blockedReason: PropTypes.string,
  onEdit: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
};

export default function ContractListCard({
  list,
  items,
  coverageOf,
  onGo,
  showActions = false,
  blockedReason = null,
  onAdd,
  onEdit,
  onRemove,
  sx,
}) {
  const blocked = Boolean(blockedReason);
  const columns = [
    list.idWidth,
    "minmax(0, 1fr)",
    coverageOf ? "auto" : null,
    showActions ? "auto" : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <SectionCard
      roomy
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
              gridTemplateColumns: columns,
              columnGap: 1.5,
              alignItems: "baseline",
              px: 2.5,
              py: 1.25,
              "&:hover .row-edit": { opacity: 1 },
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                fontFamily: MONO_FONT,
                fontWeight: list.idWeight,
                color: "text.tertiary",
              }}
            >
              {list.id(index + 1)}
            </Typography>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: list.textWeight,
                minWidth: 0,
              }}
            >
              {text}
            </Typography>
            {coverageOf && <Coverage coverage={coverageOf(text)} onGo={onGo} />}
            {showActions && (
              <RowActions
                label={`${list.noun} ${list.id(index + 1)}`}
                blockedReason={blockedReason}
                onEdit={() => onEdit(index, text)}
                onRemove={() => onRemove(index, text)}
              />
            )}
          </Box>
        ))}
      </Stack>
    </SectionCard>
  );
}

ContractListCard.propTypes = {
  list: PropTypes.shape({
    title: PropTypes.func.isRequired,
    subtitle: PropTypes.string,
    add: PropTypes.string,
    id: PropTypes.func.isRequired,
    idWidth: PropTypes.string.isRequired,
    idWeight: PropTypes.number.isRequired,
    textWeight: PropTypes.number.isRequired,
    noun: PropTypes.string,
  }).isRequired,
  items: PropTypes.arrayOf(PropTypes.string).isRequired,
  coverageOf: PropTypes.func,
  onGo: PropTypes.func,
  showActions: PropTypes.bool,
  blockedReason: PropTypes.string,
  onAdd: PropTypes.func,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  sx: PropTypes.object,
};
