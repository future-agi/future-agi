import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import {
  GatewayDetailDrawer,
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayStatusChip,
} from "../../components/frame";
import { useMCPPrompts } from "../hooks/useMCPConfig";
import KeyValueList from "../components/KeyValueList";
import SectionTitle from "../components/SectionTitle";
import { GATEWAY_MONO } from "../../utils/fonts";
import { emptyListText } from "../utils";
import RuntimeNotice from "../runtimeNotice";

const ROW_HEIGHT = 40;

function argumentList(prompt) {
  const args = prompt?.arguments || [];
  if (!args.length) return "—";
  return args.map((arg) => `${arg.name}${arg.required ? "*" : ""}`).join(", ");
}

// The prompt templates the org's MCP servers expose (prompts/list through
// the gateway). A row opens its arguments.
const PromptsTab = ({ gatewayId, runtimeOff = false }) => {
  const { data, isLoading, isError } = useMCPPrompts(gatewayId, {
    enabled: !runtimeOff,
  });
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(null);

  const prompts = useMemo(() => data || [], [data]);
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return prompts;
    return prompts.filter((p) =>
      [p.name, p.description, p.server_id].join(" ").toLowerCase().includes(q),
    );
  }, [prompts, search]);

  const columnDefs = useMemo(
    () => [
      {
        headerName: "Name",
        colId: "name",
        field: "name",
        minWidth: 200,
        flex: 1.2,
      },
      {
        headerName: "Server",
        colId: "server",
        field: "server_id",
        minWidth: 140,
      },
      {
        headerName: "Description",
        colId: "description",
        field: "description",
        valueFormatter: ({ value }) => value || "—",
        tooltipField: "description",
        minWidth: 220,
        flex: 1.6,
      },
      {
        headerName: "Arguments",
        colId: "arguments",
        valueGetter: ({ data: prompt }) => argumentList(prompt),
        headerTooltip: "* required",
        minWidth: 160,
      },
    ],
    [],
  );

  // A read that failed is not an empty list: it says so (with MCP off for
  // the deployment the read answers an empty list, not an error).
  if (isError && !runtimeOff) {
    return (
      <GatewayEmptyState
        icon="inbox"
        title={emptyListText(true, [], "prompts")}
        description="Nothing is shown until the gateway answers."
      />
    );
  }

  if (!runtimeOff && !isLoading && prompts.length === 0) {
    return (
      <GatewayEmptyState
        icon="inbox"
        title="No prompts"
        description="None of the organization's MCP servers lists a prompt template."
      />
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {runtimeOff && <RuntimeNotice />}
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search prompts"
      />
      {!isLoading && shown.length === 0 ? (
        <GatewayEmptyState
          variant={search ? "no-results" : "no-data"}
          title="No prompts in this view."
          description={
            runtimeOff && prompts.length === 0
              ? "No previously loaded prompts are available."
              : undefined
          }
          actionLabel="Clear search"
          onAction={search ? () => setSearch("") : undefined}
        />
      ) : (
        <GatewayGrid
          rowData={isLoading ? undefined : shown}
          loading={isLoading}
          columnDefs={columnDefs}
          stateKey="gateway-mcp-prompts"
          getRowId={(params) => `${params.data.server_id}:${params.data.name}`}
          onRowClicked={(event) => setOpen(event.data)}
          rowStyle={{ cursor: "pointer" }}
          height={Math.min(
            Math.max(ROW_HEIGHT * Math.max(shown.length, 3) + 42, 200),
            560,
          )}
        />
      )}

      <GatewayDetailDrawer
        open={Boolean(open)}
        onClose={() => setOpen(null)}
        title={open?.name || ""}
        subtitle="MCP prompt"
      >
        {open && (
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <KeyValueList
              rows={[
                { label: "Server", value: open.server_id },
                { label: "Description", value: open.description || "—" },
              ]}
            />
            <SectionTitle title="Arguments" component="h3" />
            {(open.arguments || []).length === 0 ? (
              <Typography typography="s2" color="text.secondary">
                No arguments are listed for this prompt.
              </Typography>
            ) : (
              (open.arguments || []).map((arg) => (
                <Box
                  key={arg.name}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    flexWrap: "wrap",
                    py: 0.5,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s1" sx={{ fontFamily: GATEWAY_MONO }}>
                    {arg.name}
                  </Typography>
                  {arg.required && (
                    <GatewayStatusChip
                      tone="neutral"
                      label="Required"
                      showIcon={false}
                    />
                  )}
                  {arg.description && (
                    <Typography typography="s2" color="text.secondary">
                      {arg.description}
                    </Typography>
                  )}
                </Box>
              ))
            )}
          </Box>
        )}
      </GatewayDetailDrawer>
    </Box>
  );
};

PromptsTab.propTypes = {
  runtimeOff: PropTypes.bool,
  gatewayId: PropTypes.string,
};

export default PromptsTab;
