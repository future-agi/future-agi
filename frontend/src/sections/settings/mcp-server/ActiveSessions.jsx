import ErrorState from "src/components/error-state";
import {
  Box,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { useMCPSessions } from "src/api/mcp";

const TRANSPORT_LABELS = {
  streamable_http: "HTTP",
  sse: "SSE",
  stdio: "Stdio",
};

function formatLastActivity(dateStr) {
  if (!dateStr) return "-";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now - date;
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

export default function ActiveSessions() {
  const theme = useTheme();
  const { data: sessions, isLoading, isError } = useMCPSessions();

  const hasSessions = Array.isArray(sessions) && sessions.length > 0;

  return (
    <Section variant="panel" sx={{ p: theme.spacing(3) }}>
      <Box
        display="flex"
        alignItems="center"
        justifyContent="space-between"
        mb={theme.spacing(2)}
      >
        <Box>
          <Typography
            sx={{
              typography: "m2",
              fontWeight: "fontWeightSemiBold",
              color: "text.primary",
            }}
          >
            Active Sessions
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              mt: theme.spacing(0.5),
            }}
          >
            Connected MCP clients auto-refresh every 30s.
          </Typography>
        </Box>
        {hasSessions && (
          <StatusBadge
            status="connected"
            label={`${sessions.length} connected`}
          />
        )}
      </Box>

      {isLoading && (
        <Box display="flex" justifyContent="center" py={4}>
          <CircularProgress size={24} />
        </Box>
      )}

      {isError && (
        <ErrorState
          title="Failed to load sessions"
          sx={{ height: "auto", flexGrow: 0 }}
        />
      )}

      {!isLoading && !isError && (
        <>
          {hasSessions ? (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Client</TableCell>
                    <TableCell>Transport</TableCell>
                    <TableCell>Status</TableCell>
                    <TableCell>Tool Calls</TableCell>
                    <TableCell>Last Activity</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {sessions.map((session, idx) => (
                    <TableRow key={session.id || idx}>
                      <TableCell>
                        <Typography
                          sx={{ typography: "s1", color: "text.primary" }}
                        >
                          {session.client_name || session.client || "Unknown"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          sx={{
                            typography: "s2",
                            color: "text.secondary",
                            textTransform: "none",
                            fontSize: 12,
                          }}
                        >
                          {TRANSPORT_LABELS[session.transport] ||
                            session.transport ||
                            "HTTP"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          status={(session.status || "active").toLowerCase()}
                        />
                      </TableCell>
                      <TableCell>
                        <Typography
                          sx={{ typography: "s2", color: "text.secondary" }}
                        >
                          {session.tool_call_count ?? 0}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography
                          sx={{ typography: "s2", color: "text.secondary" }}
                        >
                          {formatLastActivity(
                            session.last_activity_at || session.last_activity,
                          )}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : (
            <Box
              sx={{
                py: 3,
                textAlign: "center",
                border: "1px dashed",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Iconify
                icon="solar:plug-circle-linear"
                width={32}
                sx={{ color: "text.disabled", mb: 1 }}
              />
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.secondary",
                  fontWeight: "fontWeightMedium",
                }}
              >
                No active sessions
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.disabled",
                  mt: 0.5,
                }}
              >
                Sessions will appear here once you connect an IDE using the
                setup above.
              </Typography>
            </Box>
          )}
        </>
      )}
    </Section>
  );
}
