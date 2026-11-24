import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import ViewerLock from "../../components/ViewerLock";
import CodeBlock from "./CodeBlock";
import { MONO, TOOL_DRAWER_COPY as T } from "./world.constants";

const typeWord = (type) =>
  type ? T.typeWord[String(type).toLowerCase()] || String(type) : null;

function argumentLine(argument) {
  const values =
    Array.isArray(argument.values) && argument.values.length
      ? T.allowed(argument.values)
      : null;
  const bounds =
    typeof argument.min === "number" || typeof argument.max === "number"
      ? T.between(argument.min, argument.max)
      : null;
  return [
    values || typeWord(argument.type),
    bounds,
    argument.unit,
    argument.required === false ? T.optional : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function SpecRow({ label, children }) {
  return (
    <>
      <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
        {label}
      </Typography>
      <Box sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
        {children}
      </Box>
    </>
  );
}

SpecRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

export default function ToolDrawer({
  tool,
  onClose,
  onRemove,
  blockedReason,
  codePending = false,
}) {
  const args = tool?.arguments || [];
  const returned = tool?.returns || [];
  const changed = tool?.tables?.changed || [];
  const read = tool?.tables?.read || [];
  const writes = changed.length > 0 || tool?.effect === "write";
  const tables = writes ? changed : read;
  const source = tool?.entrypoint?.source;
  return (
    <SideDrawer open={Boolean(tool)} onClose={onClose} width={640}>
      {tool && (
        <Stack sx={{ height: "100%", minHeight: 0, overflow: "auto" }}>
          <Stack
            direction="row"
            alignItems="flex-start"
            spacing={1.5}
            sx={{
              pl: 2.5,
              pr: 6,
              py: 2,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box flex={1} minWidth={0}>
              <Typography
                sx={{ typography: "s1", fontWeight: 600, fontFamily: MONO }}
              >
                {tool.tool}
              </Typography>
              {tool.description && (
                <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                  {tool.description}
                </Typography>
              )}
            </Box>
          </Stack>

          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "84px minmax(0, 1fr)",
              px: 2.5,
              py: 1.5,
              columnGap: 2,
              rowGap: 0.75,
              alignItems: "baseline",
            }}
          >
            <SpecRow label={T.arguments}>
              {args.length ? (
                <Stack spacing={0.5}>
                  {args.map((argument) => (
                    <Box key={argument.name}>
                      <Box
                        component="span"
                        sx={{ fontFamily: MONO, color: "text.primary" }}
                      >
                        {argument.name}
                      </Box>
                      {argument.required === false && (
                        <Box component="span" sx={{ color: "text.disabled" }}>
                          ?
                        </Box>
                      )}
                      {argumentLine(argument) && (
                        <Box component="span" sx={{ ml: 1 }}>
                          {argumentLine(argument)}
                        </Box>
                      )}
                    </Box>
                  ))}
                </Stack>
              ) : (
                T.none
              )}
            </SpecRow>
            <SpecRow label={T.returns}>
              {returned.length ? (
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {returned
                    .map((one) =>
                      one.kind ? `${one.name} (${one.kind})` : one.name,
                    )
                    .join(", ")}
                </Box>
              ) : (
                T.none
              )}
            </SpecRow>
            <SpecRow label={writes ? T.changes : T.reads}>
              {tables.length ? (
                <Box component="span" sx={{ fontFamily: MONO }}>
                  {tables.join(", ")}
                </Box>
              ) : (
                T.noTables
              )}
            </SpecRow>
            {tool.reset_between_trials && (
              <SpecRow label={T.reset}>{T.restored}</SpecRow>
            )}
          </Box>

          {onRemove && (
            <Box
              sx={{
                px: 2.5,
                py: 1.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <ViewerLock
                locked={Boolean(blockedReason)}
                title={blockedReason || T.remove}
              >
                <Button
                  size="small"
                  variant="outlined"
                  disabled={Boolean(blockedReason)}
                  onClick={() => onRemove(tool)}
                  startIcon={
                    <Iconify
                      icon="solar:trash-bin-minimalistic-linear"
                      width={16}
                    />
                  }
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  {T.remove}
                </Button>
              </ViewerLock>
            </Box>
          )}

          {!source?.code && codePending && (
            <Typography
              sx={{
                typography: "s3",
                color: "text.tertiary",
                px: 2.5,
                py: 1.5,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {T.codePending}
            </Typography>
          )}
          {source?.code && (
            <CodeBlock
              file={
                source.line_start
                  ? `${source.path}:${source.line_start}-${source.line_end}`
                  : source.path
              }
              code={source.code}
            />
          )}
        </Stack>
      )}
    </SideDrawer>
  );
}

ToolDrawer.propTypes = {
  tool: PropTypes.shape({
    tool: PropTypes.string,
    description: PropTypes.string,
    arguments: PropTypes.arrayOf(PropTypes.object),
    returns: PropTypes.arrayOf(PropTypes.object),
    effect: PropTypes.string,
    reset_between_trials: PropTypes.bool,
    tables: PropTypes.shape({
      read: PropTypes.arrayOf(PropTypes.string),
      changed: PropTypes.arrayOf(PropTypes.string),
    }),
    entrypoint: PropTypes.shape({
      source: PropTypes.shape({
        path: PropTypes.string,
        line_start: PropTypes.number,
        line_end: PropTypes.number,
        code: PropTypes.string,
      }),
    }),
  }),
  onClose: PropTypes.func.isRequired,
  onRemove: PropTypes.func,
  blockedReason: PropTypes.string,
  codePending: PropTypes.bool,
};
