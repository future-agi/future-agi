import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { Box, ButtonBase, Stack, Tab, Typography } from "@mui/material";
import EmptyState from "src/components/empty-state";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import { SegmentedTabs } from "src/components/tabs/tabs";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";
import { CONNECT_GROUPS, groupItems, searchGroups } from "./connectSources";
import { LogoTile, Panel } from "./connectBits";
import { focusRing } from "./connectStyles";
import { PANEL_SX } from "./connectStyles";

function CatalogRow({ item, selected, onPick, single }) {
  const inert = !item.available;
  return (
    <ButtonBase
      disabled={inert}
      onClick={() => onPick(item)}
      aria-pressed={selected}
      sx={{
        width: "100%",
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 1.25,
        px: 1,
        minHeight: single ? 36 : 48,
        borderRadius: "6px",
        bgcolor: selected ? "action.selected" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
        "&:hover .catalog-connect, &.Mui-focusVisible .catalog-connect": {
          borderColor: "text.secondary",
        },
        "&.Mui-focusVisible": focusRing,
        "&.Mui-disabled": { opacity: 0.6 },
      }}
    >
      <LogoTile icon={item.icon} name={item.name} muted={inert} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          noWrap
          sx={{
            typography: "s1",
            fontWeight: 500,
            color: inert ? "text.secondary" : "text.primary",
          }}
        >
          {item.name}
        </Typography>
        {!single && (
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {inert ? "Coming soon" : item.next}
          </Typography>
        )}
      </Box>
      {inert && single && <ComingSoonChip compact />}
      {!inert && (
        <Box
          className="catalog-connect"
          sx={{
            flexShrink: 0,
            display: "inline-flex",
            alignItems: "center",
            px: 1.25,
            height: 28,
            borderRadius: "6px",
            border: "1px solid",
            borderColor: "divider",
            typography: "s3",
            fontWeight: 500,
            color: "text.primary",
          }}
        >
          {selected ? "Selected" : "Connect"}
        </Box>
      )}
    </ButtonBase>
  );
}
CatalogRow.propTypes = {
  item: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
  single: PropTypes.bool,
};

function RowGrid({ items, columns = 2, single = false, pickedId, onPick }) {
  return (
    <Box
      sx={(t) => ({
        display: "grid",
        gridTemplateColumns: "1fr",
        p: 1,
        [t.breakpoints.up("md")]: {
          gridTemplateColumns: `repeat(${columns}, 1fr)`,
          columnGap: 4,
        },
      })}
    >
      {items.map((item) => (
        <CatalogRow
          key={item.id}
          item={item}
          single={single}
          selected={pickedId === item.id}
          onPick={onPick}
        />
      ))}
    </Box>
  );
}
RowGrid.propTypes = {
  items: PropTypes.array.isRequired,
  columns: PropTypes.number,
  single: PropTypes.bool,
  pickedId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};

function DropZone({ item, selected, onPick }) {
  return (
    <ButtonBase
      onClick={() => onPick(item)}
      aria-pressed={selected}
      data-testid="connect-upload-folder"
      sx={{
        m: 1,
        display: "flex",
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 1.5,
        px: 2,
        py: 1.5,
        borderRadius: "6px",
        border: "1px dashed",
        borderColor: selected ? "text.secondary" : "divider",
        bgcolor: selected ? "action.selected" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      <LogoTile icon={item.icon} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s1", fontWeight: 500 }}>
          {item.name}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Up to 200 MB · secrets files are left out
        </Typography>
      </Box>
    </ButtonBase>
  );
}
DropZone.propTypes = {
  item: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onPick: PropTypes.func.isRequired,
};

export default function ConnectCatalog({ pickedId, onPick }) {
  const [query, setQuery] = useState("");
  const [groupId, setGroupId] = useState(CONNECT_GROUPS[0].id);
  const searching = !!query.trim();
  const results = useMemo(
    () => (searching ? searchGroups(query) : []),
    [query, searching],
  );
  const group = CONNECT_GROUPS.find((candidate) => candidate.id === groupId);

  let list;
  if (searching && !results.length) {
    list = (
      <Box sx={PANEL_SX}>
        <EmptyState
          variant="no-results"
          icon="solar:magnifer-linear"
          title={`Nothing matches "${query.trim()}"`}
          description="Try a platform, a framework or where the code lives."
        />
      </Box>
    );
  } else if (searching) {
    list = results.map((result) => (
      <Panel
        key={result.id}
        title={`${result.title} · ${result.items.length}`}
        reach={result.reach}
      >
        <RowGrid items={result.items} pickedId={pickedId} onPick={onPick} />
      </Panel>
    ));
  } else if (group.sections) {
    list = group.sections.map((section) => (
      <Panel key={section.id} title={section.title} reach={section.reach}>
        {section.dropzone ? (
          <DropZone
            item={section.items[0]}
            selected={pickedId === section.items[0].id}
            onPick={onPick}
          />
        ) : (
          <RowGrid
            items={section.items}
            columns={section.columns}
            single={section.id === "sdk"}
            pickedId={pickedId}
            onPick={onPick}
          />
        )}
      </Panel>
    ));
  } else {
    list = (
      <Panel reach={group.reach}>
        <RowGrid items={group.items} pickedId={pickedId} onPick={onPick} />
      </Panel>
    );
  }

  return (
    <Stack spacing={1.5}>
      <FormSearchField
        fullWidth
        searchQuery={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search ways to connect: GitHub, Vapi, LangGraph, endpoint"
        inputProps={{ "aria-label": "Search ways to connect" }}
      />
      {!searching && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
        >
          <SegmentedTabs
            value={groupId}
            onChange={(_event, next) => setGroupId(next)}
          >
            {CONNECT_GROUPS.map((candidate) => (
              <Tab
                key={candidate.id}
                value={candidate.id}
                label={`${candidate.title} ${groupItems(candidate).length}`}
                disableRipple
              />
            ))}
          </SegmentedTabs>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              No agent yet? Build one in Future AGI
            </Typography>
            <ComingSoonChip compact />
          </Stack>
        </Stack>
      )}
      {list}
    </Stack>
  );
}
ConnectCatalog.propTypes = {
  pickedId: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};
