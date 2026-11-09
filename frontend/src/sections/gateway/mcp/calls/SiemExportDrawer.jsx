import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import ConfirmDialog from "../../components/ConfirmDialog";
import { GatewayDetailDrawer } from "../../components/frame";
import { apiErrorMessage } from "../api";
import SectionTitle from "../components/SectionTitle";
import {
  siemWrites,
  useSiemDeliveries,
  useSiemDestinations,
  useSiemWrite,
} from "../hooks/useSiemExport";
import SiemDestinationForm from "./SiemDestinationForm";
import SiemDestination from "./SiemDestination";
import { SIEM_STREAMS } from "./siemOptions";
import { formatCallTime } from "./toolCalls";

// SIEM export (G16-SIEM): where this organization's MCP tool-call audit
// events and spans go. Owners and Admins add destinations (they start off),
// turn them on or off (audited), and see, retry or discard dead letters.
// Exports hold metadata only; the server enforces every rule shown here.

const streamLabels = (streams) =>
  (streams || [])
    .map((s) => SIEM_STREAMS.find((k) => k.value === s)?.label || s)
    .join(", ");

const ROW_SX = {
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
  p: 1.5,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 1.5,
  flexWrap: "wrap",
};

function Section({ title, children }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <SectionTitle title={title} component="h3" />
      {children}
    </Box>
  );
}

Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

function DeadLetter({ delivery, onDiscard, canManage }) {
  const retry = useSiemWrite(siemWrites.retry);
  return (
    <Box sx={ROW_SX}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 0.25,
          minWidth: 0,
        }}
      >
        <Typography typography="s1">
          {`${delivery.destination_name} · ${streamLabels([delivery.stream])}`}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {`${delivery.event_count} calls · ${delivery.last_error || "No error recorded"} · ${delivery.attempts} of ${delivery.max_attempts} attempts · ${formatCallTime(delivery.last_attempt_at || delivery.created_at)}`}
        </Typography>
        {retry.isError && (
          <Typography typography="s2" color="error.main" role="alert">
            {apiErrorMessage(retry.error, "The delivery was not retried.")}
          </Typography>
        )}
      </Box>
      {canManage && (
        <Box sx={{ display: "flex", gap: 1 }}>
          <Button
            size="small"
            variant="outlined"
            disabled={retry.isPending}
            onClick={() => retry.mutate(delivery.id)}
          >
            Retry
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => onDiscard(delivery)}
          >
            Discard
          </Button>
        </Box>
      )}
    </Box>
  );
}

DeadLetter.propTypes = {
  canManage: PropTypes.bool,
  delivery: PropTypes.object.isRequired,
  onDiscard: PropTypes.func.isRequired,
};

export default function SiemExportDrawer({ open, onClose }) {
  const destinations = useSiemDestinations(open);
  const forbidden = destinations.error?.response?.status === 403;
  const deliveries = useSiemDeliveries(
    open && !forbidden && destinations.isSuccess,
  );
  const failures = useSiemDeliveries(
    open && !forbidden && destinations.isSuccess,
    "dead_letter",
  );
  const discard = useSiemWrite(siemWrites.discard);
  const [discarding, setDiscarding] = useState(null);
  const result = destinations.data;
  const list = result?.destinations || [];
  const canManage = result?.can_manage === true;
  // Keep the failure query separate: 100 newer successes must not hide a
  // dead letter that still needs attention.
  const deadLetters = failures.data || [];

  let content;
  if (forbidden) {
    content = (
      <Typography typography="s2" color="text.secondary">
        Only an organization Owner or Admin can manage SIEM export.
      </Typography>
    );
  } else if (destinations.isError) {
    content = (
      <Typography typography="s2" color="error.main" role="alert">
        {apiErrorMessage(
          destinations.error,
          "SIEM export could not be loaded.",
        )}
      </Typography>
    );
  } else if (destinations.isLoading) {
    content = <Skeleton variant="rounded" height={120} />;
  } else {
    content = (
      <>
        {!result?.export_available && (
          <Typography typography="s2" color="text.secondary">
            SIEM export is off on this deployment. You can add destinations;
            they stay off until the operator turns export on.
          </Typography>
        )}
        {!canManage && (
          <Typography typography="s2" color="text.secondary">
            Read-only. Only an organization Owner or Admin can manage SIEM
            export.
          </Typography>
        )}
        <Section title="Destinations">
          {list.length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              No destinations yet.
            </Typography>
          ) : (
            list.map((destination) => (
              <SiemDestination
                canManage={canManage}
                key={destination.id}
                destination={destination}
                exportAvailable={Boolean(result?.export_available)}
              />
            ))
          )}
        </Section>
        {canManage && (
          <Section title="Add a destination">
            <SiemDestinationForm disabled={list.length >= 5} />
          </Section>
        )}
        <Section title="Recent deliveries">
          {deliveries.isError ? (
            <Typography typography="s2" color="error.main" role="alert">
              Delivery status could not be loaded.
            </Typography>
          ) : deliveries.isLoading ? (
            <Skeleton variant="rounded" height={60} />
          ) : (deliveries.data || []).length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              No deliveries yet.
            </Typography>
          ) : (
            deliveries.data.map((row) => (
              <Box key={row.id} sx={ROW_SX}>
                <Typography typography="s2">{`Delivery to ${row.destination_name}`}</Typography>
                <Typography typography="s2">{`${row.status === "dead_letter" ? "Failed (dead letter)" : row.status} · ${row.event_count} calls · ${row.attempts}/${row.max_attempts} attempts`}</Typography>
              </Box>
            ))
          )}
        </Section>
        <Section title="Dead letters">
          {failures.isError ? (
            <Typography typography="s2" color="error.main" role="alert">
              Dead letters could not be loaded.
            </Typography>
          ) : failures.isLoading ? (
            <Skeleton variant="rounded" height={60} />
          ) : deadLetters.length === 0 ? (
            <Typography typography="s2" color="text.secondary">
              No dead letters.
            </Typography>
          ) : (
            deadLetters.map((delivery) => (
              <DeadLetter
                key={delivery.id}
                delivery={delivery}
                onDiscard={setDiscarding}
                canManage={canManage}
              />
            ))
          )}
        </Section>
      </>
    );
  }

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      title="SIEM export"
      subtitle="Tool-call audit events and spans, sent to your organization's collector"
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
        <Typography typography="s2" color="text.secondary">
          Exports hold metadata only: identifiers, outcome, timing, size and
          cost. Arguments, results and error text are never exported. Every
          change here is audited.
        </Typography>
        {content}
      </Box>
      <ConfirmDialog
        open={Boolean(discarding)}
        onClose={() => setDiscarding(null)}
        onConfirm={async () => {
          await discard.mutateAsync(discarding.id).catch(() => {});
          setDiscarding(null);
        }}
        title="Discard dead letter"
        message={
          discarding
            ? `The ${discarding.event_count} calls in this delivery are not sent to ${discarding.destination_name}.`
            : ""
        }
        confirmLabel="Discard"
        isLoading={discard.isPending}
      />
    </GatewayDetailDrawer>
  );
}

SiemExportDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
};
