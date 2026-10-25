import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import DigestLine from "./RunDigest";
import { creditsLabel } from "../_mock/metering";

/*
  What a finished run consumed, as recorded on it (PRD v6 · AC-16.9). Calls
  that weren't measured meter nothing, and the line says so; a customer's own
  sandbox records zero sandbox minutes.
*/
function MeterTooltip({ metering }) {
  return (
    <Stack spacing={0.5} sx={{ py: 0.25 }}>
      {/* Spend is said in credits, as everywhere else. */}
      {metering.detail.map((d, i) => {
        const usd = metering.lines?.[i]?.usd;
        const price =
          usd == null
            ? metering.lines?.[i]?.amount
              ? "not priced yet"
              : "0 credits"
            : creditsLabel(usd);
        return (
          <Stack
            key={d.label}
            direction="row"
            spacing={2}
            justifyContent="space-between"
          >
            <span>{d.label}</span>
            <span>{`${d.amount} · ${price}`}</span>
          </Stack>
        );
      })}
      <span>{`≈ ${creditsLabel(metering.usd || 0)} in all${metering.sandboxTbd ? ", plus run time not priced yet" : ""}`}</span>
      {metering.judged.length > 0 && (
        <span>{`Credits: ${metering.judged.join(", ")} (managed judge)`}</span>
      )}
      {metering.notes.map((n) => (
        <span key={n}>{n}</span>
      ))}
    </Stack>
  );
}
MeterTooltip.propTypes = { metering: PropTypes.object };

/** "1.2M tokens · 340 credits" with the breakdown on hover — for the run header. */
export default function MeteredText({ metering }) {
  if (!metering?.line) return null;
  return (
    <Tooltip
      classes={{ tooltip: "ds-hovercard" }}
      arrow
      placement="bottom-start"
      title={<MeterTooltip metering={metering} />}
    >
      <Box component="span" sx={{ cursor: "default" }}>
        {metering.line}
      </Box>
    </Tooltip>
  );
}
MeteredText.propTypes = { metering: PropTypes.object };

/** The foot of Insights: what the run metered, and the digest it sent. */
export function RunRecordFooter({
  metering,
  digest,
  destinations,
  sentAt,
  inline = false,
}) {
  if (!metering && !digest) return null;
  return (
    <Stack
      direction={inline ? { xs: "column", md: "row" } : "column"}
      spacing={inline ? { xs: 0.5, md: 2 } : 0.75}
      alignItems={inline ? { md: "center" } : "stretch"}
      sx={
        inline
          ? { minWidth: 0 }
          : {
              px: 1.75,
              py: 1.25,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }
      }
    >
      {metering?.line && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ minWidth: 0 }}
        >
          <Iconify
            icon="solar:bill-list-linear"
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
          <Typography
            sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
          >
            <Box component="span" sx={{ color: "text.tertiary" }}>
              Metered ·{" "}
            </Box>
            <MeteredText metering={metering} />
            {metering.notes.map((n) => ` · ${n}`).join("")}
          </Typography>
        </Stack>
      )}
      <DigestLine digest={digest} destinations={destinations} sentAt={sentAt} />
    </Stack>
  );
}
RunRecordFooter.propTypes = {
  metering: PropTypes.object,
  digest: PropTypes.object,
  destinations: PropTypes.object,
  sentAt: PropTypes.string,
  /* One line, no frame — the foot of the verdict card. */
  inline: PropTypes.bool,
};
