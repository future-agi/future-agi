import PropTypes from "prop-types";
import { useState } from "react";
import { Box, ButtonBase, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SoonTag from "../components/SoonTag";
import LogoTile from "./LogoTile";
import UiOnlyFlag from "../components/UiOnlyFlag";
import {
  CATALOG_ITEMS,
  CATALOG_TABS,
  CODE_SECTIONS,
  searchCatalog,
} from "./buildDrawerCatalog";
import {
  CONNECT_BTN_SX,
  LINK_SX,
  OUTLINED_BTN_SX,
  PANEL_SX,
  T12,
  T13,
  chipSx,
  focusRing,
} from "./drawerStyles";
import { SectionHead } from "./starts/startBits";

// One way in: a button that opens its setup. A UI-only one says so beside
// Connect.
function WayRow({ item, onPick, compact }) {
  const live = !!item.setup;
  const uiOnly = !!item.setup?.uiOnly;
  const body = (
    <>
      <LogoTile logo={item.logo} icon={item.icon} />
      <Box flex={1} minWidth={0} sx={{ textAlign: "left" }}>
        <Typography noWrap sx={{ ...T13, fontWeight: 500 }}>
          {item.name}
        </Typography>
        {!compact && item.next && (
          <Typography noWrap sx={{ ...T12, color: "text.secondary" }}>
            {item.next}
          </Typography>
        )}
      </Box>
      {uiOnly && <UiOnlyFlag />}
      {live ? (
        <Box
          component="span"
          sx={{
            ...CONNECT_BTN_SX,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          Connect
        </Box>
      ) : (
        <SoonTag />
      )}
    </>
  );
  const rowSx = {
    display: "flex",
    alignItems: "center",
    gap: 1.25,
    width: "100%",
    minWidth: 0,
    px: 1,
    py: compact ? 0.5 : 0.75,
    borderRadius: "6px",
  };
  return (
    <Box component="li" aria-label={item.name} sx={{ minWidth: 0 }}>
      {live ? (
        <ButtonBase
          onClick={() => onPick(item)}
          aria-label={`Connect ${item.name}`}
          sx={{
            ...rowSx,
            "&:hover": { bgcolor: "action.hover" },
            "&.Mui-focusVisible": focusRing,
          }}
        >
          {body}
        </ButtonBase>
      ) : (
        <Box sx={rowSx}>{body}</Box>
      )}
    </Box>
  );
}
WayRow.propTypes = {
  item: PropTypes.object.isRequired,
  onPick: PropTypes.func.isRequired,
  compact: PropTypes.bool,
};

// A bordered group of ways, two to a line, under an optional one-line head.
function WayGroup({ title, line, items, onPick, compact = false }) {
  return (
    <Box sx={PANEL_SX}>
      {title && (
        <Stack
          direction="row"
          alignItems="baseline"
          spacing={1.25}
          sx={{
            px: 2,
            py: 1.125,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexWrap: "wrap",
          }}
        >
          <Typography sx={{ ...T13, fontWeight: 600 }}>{title}</Typography>
          {line && (
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              {line}
            </Typography>
          )}
        </Stack>
      )}
      <Box
        component="ul"
        sx={{
          listStyle: "none",
          m: 0,
          px: 1,
          py: 1,
          display: "grid",
          columnGap: 4,
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
        }}
      >
        {items.map((it) => (
          <WayRow key={it.id} item={it} onPick={onPick} compact={compact} />
        ))}
      </Box>
    </Box>
  );
}
WayGroup.propTypes = {
  title: PropTypes.string,
  line: PropTypes.string,
  items: PropTypes.array.isRequired,
  onPick: PropTypes.func.isRequired,
  compact: PropTypes.bool,
};

// "Without a repository": one wide drop row with its own Upload button.
function UploadGroup({ item, onPick }) {
  const section = CODE_SECTIONS.find((s) => s.id === "upload");
  return (
    <Box sx={PANEL_SX}>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1.25}
        sx={{
          px: 2,
          py: 1.125,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ ...T13, fontWeight: 600 }}>
          {section.title}
        </Typography>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          {section.line}
        </Typography>
      </Stack>
      <Box component="ul" sx={{ listStyle: "none", m: 0, p: 1 }}>
        <Box component="li" aria-label={item.name}>
          <ButtonBase
            onClick={() => onPick(item)}
            aria-label="Upload a folder"
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              width: "100%",
              px: 1,
              py: 0.75,
              borderRadius: "6px",
              textAlign: "left",
              "&:hover": { bgcolor: "action.hover" },
              "&.Mui-focusVisible": focusRing,
            }}
          >
            <LogoTile icon={item.icon} />
            <Box flex={1} minWidth={0}>
              <Typography sx={{ ...T13, fontWeight: 500 }}>
                {item.name}
              </Typography>
              <Typography sx={{ ...T12, color: "text.secondary" }}>
                {item.next}
              </Typography>
            </Box>
            <Box
              component="span"
              sx={{
                ...OUTLINED_BTN_SX,
                border: "1px solid",
                borderColor: "divider",
                display: "inline-flex",
                alignItems: "center",
                gap: 0.75,
              }}
            >
              <Iconify icon="solar:upload-minimalistic-linear" width={14} />
              Upload
            </Box>
          </ButtonBase>
        </Box>
      </Box>
    </Box>
  );
}
UploadGroup.propTypes = {
  item: PropTypes.object.isRequired,
  onPick: PropTypes.func.isRequired,
};

function SearchBox({ value, onChange }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{ ...PANEL_SX, borderRadius: "6px", height: 44, px: 1.5 }}
    >
      <Iconify
        icon="solar:magnifer-linear"
        width={16}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <InputBase
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search ways to connect: GitHub, Vapi, LangGraph, endpoint…"
        inputProps={{ "aria-label": "Search ways to connect" }}
        sx={{ flex: 1, ...T13 }}
      />
    </Stack>
  );
}
SearchBox.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

/**
 * Connect your agent: search, Vel's three tabs and every way in them.
 * `items` narrows the ways (to those for one type of agent); a tab left empty
 * isn't shown. `head` is off where the drawer's header already says what this
 * is, and the MCP link shows only with `onPickMcp`. `aside` sits at the right
 * end of the tabs.
 */
export default function ConnectSection({
  onPick,
  items = CATALOG_ITEMS,
  head = true,
  onPickMcp,
  aside = null,
}) {
  const inTab = (id) => items.filter((it) => it.tab === id);
  const tabs = CATALOG_TABS.filter((t) => inTab(t.id).length);
  const [tab, setTab] = useState(tabs[0]?.id);
  const [query, setQuery] = useState("");
  const searching = !!query.trim();
  const matches = searchCatalog(items, query);

  let body;
  if (searching) {
    body = matches.length ? (
      <WayGroup items={matches} onPick={onPick} />
    ) : (
      <Typography sx={{ ...T13, color: "text.secondary", py: 2 }}>
        No way to connect matches &ldquo;{query.trim()}&rdquo;.
      </Typography>
    );
  } else if (tab === "code") {
    const code = inTab("code");
    const upload = code.find((it) => it.section === "upload");
    body = (
      <Stack spacing={1.25}>
        {CODE_SECTIONS.filter((s) => s.id !== "upload").map((s) => {
          const ofSection = code.filter((it) => it.section === s.id);
          return ofSection.length ? (
            <WayGroup
              key={s.id}
              title={s.title}
              line={s.line}
              items={ofSection}
              onPick={onPick}
              compact={s.id === "sdk"}
            />
          ) : null;
        })}
        {upload && <UploadGroup item={upload} onPick={onPick} />}
      </Stack>
    );
  } else {
    body = <WayGroup items={inTab(tab)} onPick={onPick} />;
  }

  return (
    <Box>
      {head && (
        <SectionHead
          title="Connect your agent"
          subtitle="Pick where it runs today. It joins Agents, then you build its environment here."
        />
      )}
      <Stack spacing={1.5}>
        <SearchBox value={query} onChange={setQuery} />
        {!searching && (
          <Stack direction="row" alignItems="center" spacing={2}>
            <Stack
              direction="row"
              spacing={0.75}
              role="tablist"
              aria-label="Ways to connect"
              sx={{ flex: 1, minWidth: 0 }}
            >
              {tabs.map((t) => {
                const on = tab === t.id;
                return (
                  <ButtonBase
                    key={t.id}
                    role="tab"
                    aria-selected={on}
                    onClick={() => setTab(t.id)}
                    sx={chipSx(on)}
                  >
                    {t.label}{" "}
                    <Box
                      component="span"
                      sx={{ color: "text.secondary", fontWeight: 400 }}
                    >
                      {inTab(t.id).length}
                    </Box>
                  </ButtonBase>
                );
              })}
            </Stack>
            {aside}
          </Stack>
        )}
        {body}
      </Stack>
      {onPickMcp && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mt: 1.25 }}
        >
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            {"Tools on an MCP server? "}
            <ButtonBase onClick={onPickMcp} sx={LINK_SX}>
              Use them as an agent&apos;s world
            </ButtonBase>
          </Typography>
          <UiOnlyFlag />
        </Stack>
      )}
    </Box>
  );
}
ConnectSection.propTypes = {
  onPick: PropTypes.func.isRequired,
  items: PropTypes.array,
  head: PropTypes.bool,
  onPickMcp: PropTypes.func,
  aside: PropTypes.node,
};
