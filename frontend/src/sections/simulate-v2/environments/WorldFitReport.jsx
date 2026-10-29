import PropTypes from "prop-types";
import { Box, MenuItem, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { agentToolNamesOf } from "../_mock/worldFit";

const KIND = {
  matched: { label: "Matched", tone: "text.secondary" },
  mapped: { label: "Mapped — review", tone: "text.primary" },
  missing: { label: "Not available", tone: "status.warning.fg" },
};

/**
 * Review fit (roadmap catalogue.md): your agent against the world's fixed
 * suite. Each world tool binds to one of your agent's tools — matched by
 * name, mapped by meaning (to review), or not available, whose cases stay
 * listed as unmeasured. The suite is never rewritten to make the agent fit.
 */
export default function WorldFitReport({ template, agent, fit, onBind }) {
  const agentTools = agentToolNamesOf(agent);
  const total = fit.total;
  return (
    <Stack spacing={1.5}>
      {/* The verdict first: Ready runs the whole supported suite; Partial says what isn't measured. */}
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Iconify
          icon={
            fit.verdict === "ready"
              ? "solar:check-circle-linear"
              : "solar:danger-triangle-linear"
          }
          width={16}
          sx={{
            color:
              fit.verdict === "ready"
                ? "status.success.fg"
                : "status.warning.fg",
            flexShrink: 0,
            mt: "2px",
          }}
        />
        <Typography sx={{ typography: "s1" }}>
          <Box component="span" sx={{ fontWeight: 600 }}>
            {fit.verdict === "ready" ? "Ready" : "Partial"}
          </Box>
          {fit.verdict === "ready"
            ? ` — all ${total} cases run on ${agent.name}, unchanged.`
            : ` — ${fit.unmeasured.length} of ${total} cases can't be measured${fit.channel.ok ? "" : `, and ${agent.name} is a ${fit.channel.agent} agent in a ${fit.channel.world} world`}. They stay listed, never dropped.`}
        </Typography>
      </Stack>

      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        <Stack
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              width: 190,
              flexShrink: 0,
            }}
          >
            World tool
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}
          >{`${agent.name}'s tool`}</Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.tertiary",
              width: 120,
              flexShrink: 0,
            }}
          >
            Fit
          </Typography>
        </Stack>
        {fit.rows.map((r, i) => (
          <Stack
            key={r.world}
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{
              px: 1.5,
              py: 0.5,
              ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
            }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s1",
                fontFamily: "monospace",
                width: 190,
                flexShrink: 0,
              }}
            >
              {r.world}
            </Typography>
            <Box flex={1} minWidth={0}>
              <Select
                size="small"
                fullWidth
                value={r.agent || ""}
                displayEmpty
                onChange={(e) => onBind(r.world, e.target.value || null)}
                sx={{
                  typography: "s1",
                  fontFamily: "monospace",
                  "& .MuiSelect-select": { py: 0.5 },
                }}
              >
                <MenuItem value="" sx={{ typography: "s1" }}>
                  Not available
                </MenuItem>
                {agentTools.map((a) => (
                  <MenuItem
                    key={a}
                    value={a}
                    sx={{ typography: "s1", fontFamily: "monospace" }}
                  >
                    {a}
                  </MenuItem>
                ))}
              </Select>
            </Box>
            <Typography
              sx={{
                typography: "s3",
                color: KIND[r.kind].tone,
                width: 120,
                flexShrink: 0,
              }}
            >
              {KIND[r.kind].label}
            </Typography>
          </Stack>
        ))}
      </Box>

      {fit.mapped.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Review each mapping for identity and refusal as well as fields — e.g. ${fit.mapped[0].agent} must look up the same record and refuse the same requests as ${fit.mapped[0].world}.`}
        </Typography>
      )}

      {fit.extra.length > 0 && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`${agent.name}'s ${fit.extra.join(", ")} ${fit.extra.length === 1 ? "isn't" : "aren't"} in this world — ${fit.live ? "calls to them reach your real systems on a live run" : "the world doesn't answer them, so a case that calls them fails"}.`}
        </Typography>
      )}

      <Typography sx={{ typography: "s3", color: "text.secondary" }}>
        {`The world's ${(template.rules || []).length} rules and checks stay as they are. ${fit.live ? "Live: only the starting state is checked." : "Sealed: the world resets for every case."}`}
      </Typography>

      {/* Partial: what isn't measured, and how to repair it. */}
      {fit.unmeasured.length > 0 && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            px: 1.5,
            py: 1,
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
            {`Unmeasured · ${fit.unmeasured.length}`}
          </Typography>
          {fit.unmeasured.slice(0, 5).map((s) => (
            <Typography
              key={s.id}
              noWrap
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {s.title}
            </Typography>
          ))}
          {fit.unmeasured.length > 5 && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {`+${fit.unmeasured.length - 5} more`}
            </Typography>
          )}
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
          >
            To measure them: map a tool above, add the tool to your agent, or
            adapt the scenarios to your agent — that makes a fork whose results
            don&apos;t compare with the sample.
          </Typography>
        </Box>
      )}
    </Stack>
  );
}
WorldFitReport.propTypes = {
  template: PropTypes.object.isRequired,
  agent: PropTypes.object.isRequired,
  fit: PropTypes.object.isRequired,
  onBind: PropTypes.func.isRequired,
};
