import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { GatewayDetailDrawer } from "../../components/frame";
import GatewayAdminOnly from "../../components/GatewayAdminOnly";
import { KIND_LABELS, STAGE_LABELS } from "../catalog";
import {
  formIsComplete,
  formValuesFor,
  storedActionOf,
} from "../catalog/rule-model";
import { incompleteReason } from "../catalog/form-model";
import CheckForm from "./CheckForm";
import { isSavedButNotApplied } from "../../providers/hooks/useGatewayConfig";
import { saveErrorMessage } from "../../hooks/useConfigPatch";
import { GUARDRAILS_OFF_BANNER } from "../../utils/guardrailPipeline";
import { exampleForCheck } from "../test/examples";

// Where the gateway runs the check. It never receives a stage saved with a
// rule: an organization's check runs where its type runs.
const STAGE_SENTENCE = {
  request: "Reads the request, before the model is called.",
  // Blocking answer checks hold the stream until their checks complete.
  response:
    "Reads the model's answer. A block refuses it with a 403. A streamed answer is held until a blocking check passes; a blocked stream carries none of the answer and ends with the guardrail's error.",
};
// While the gateway runs no check, where it would run, and no refusal.
const STAGE_SENTENCE_WHEN_OFF = {
  request: STAGE_SENTENCE.request,
  response: "Reads the model's answer.",
};

function Stat({ label, value }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s3" color="text.secondary">
        {label}
      </Typography>
      <Typography typography="m3" color="text.primary">
        {value}
      </Typography>
    </Box>
  );
}

Stat.propTypes = { label: PropTypes.string, value: PropTypes.node };

/**
 * One check's settings, opened from its row (`row`) or from the catalog
 * (`meta` with no row: a new rule). `onSave(form, intent?)` and `onRemove()`
 * return promises; the drawer closes when they resolve. Only the Rules host
 * opts into `allowTry` and handles the optional { tryAfterSave: true } intent.
 */
export default function RuleDrawer({
  open,
  meta,
  row,
  onClose,
  onSave,
  onRemove,
  draft = false,
  asMap = false,
  canEdit = true,
  enforced = true,
  allowTry = false,
}) {
  const isNew = !row;
  const label = row?.label || meta?.label || row?.id || "";
  const [values, setValues] = useState(null);
  const [busy, setBusy] = useState(false);
  const [unapplied, setUnapplied] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const saving = useRef(false);
  const hasSample = Boolean(exampleForCheck(row?.id || meta?.name));

  useEffect(() => {
    if (!open) return;
    setValues(
      formValuesFor(
        meta,
        row?.check || null,
        (check) => storedActionOf(check, { asMap }),
        asMap ? 0 : 0.8,
      ),
    );
    setConfirmRemove(false);
    setBusy(false);
    saving.current = false;
    setUnapplied(null);
  }, [open, meta, row, asMap]);

  // A save the gateway did not apply was stored: the drawer stays open and
  // says so, as every gateway config dialog does.
  const finish = (action) => {
    if (saving.current || !canEdit) return;
    saving.current = true;
    setBusy(true);
    setUnapplied(null);
    Promise.resolve()
      .then(action)
      .then(onClose)
      .catch((err) => {
        setUnapplied(
          isSavedButNotApplied(err)
            ? err.message
            : saveErrorMessage(err, "Could not save this rule."),
        );
        saving.current = false;
        setBusy(false);
      });
  };

  const complete = values ? formIsComplete(meta, values) : false;
  const missing = values && !complete ? incompleteReason(meta, values) : null;
  const stage = row?.stage || meta?.stage || "request";
  const kind = row?.kind || meta?.kind || "custom";

  return (
    <GatewayDetailDrawer
      open={open}
      onClose={onClose}
      title={label}
      subtitle={`${KIND_LABELS[kind]} check · ${STAGE_LABELS[stage]}`}
      width={480}
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <Box>
          <Typography typography="s1" color="text.primary">
            {row?.description || meta?.description}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {(enforced ? STAGE_SENTENCE : STAGE_SENTENCE_WHEN_OFF)[stage]}
          </Typography>
        </Box>

        {!enforced && <Alert severity="warning">{GUARDRAILS_OFF_BANNER}</Alert>}

        {row && !draft && (
          <Box
            role="group"
            aria-label="Rule activity, last 7 days"
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
              gap: 1.5,
              p: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
            }}
          >
            <Stat label="Matches (7d)" value={row.triggers} />
            <Stat label="Blocked" value={row.blocked} />
            <Stat
              label="Feedback accuracy"
              value={
                row.accuracy === null
                  ? "No reviews"
                  : `${Math.round(row.accuracy)}%`
              }
            />
          </Box>
        )}

        {values && (
          <CheckForm
            meta={meta}
            checkName={row?.id || meta?.name}
            values={values}
            onChange={setValues}
            disabled={busy || !canEdit}
            enforced={enforced}
            check={row?.check || null}
          />
        )}

        {row && !draft && (
          <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
            <Link
              component={RouterLink}
              href={`${paths.dashboard.gateway.guardrails.logs}?rule=${encodeURIComponent(row.id)}`}
              typography="s2"
            >
              Requests it matched
            </Link>
            <Link
              component={RouterLink}
              href={paths.dashboard.gateway.guardrails.playground}
              typography="s2"
            >
              Try a prompt
            </Link>
          </Box>
        )}

        {unapplied && <Alert severity="warning">{unapplied}</Alert>}

        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            pt: 1,
            borderTop: "1px solid",
            borderColor: "divider",
            flexWrap: "wrap",
          }}
        >
          {row && onRemove && !confirmRemove && (
            <GatewayAdminOnly allowed={canEdit}>
              <Button
                color="error"
                size="small"
                disabled={busy || !canEdit}
                onClick={() => setConfirmRemove(true)}
              >
                Remove
              </Button>
            </GatewayAdminOnly>
          )}
          {confirmRemove && (
            <>
              <Typography typography="s2" color="text.secondary">
                Remove {label} from this gateway?
              </Typography>
              <Button
                color="error"
                size="small"
                variant="outlined"
                disabled={busy}
                onClick={() => finish(onRemove)}
              >
                Remove
              </Button>
              <Button
                size="small"
                disabled={busy}
                onClick={() => setConfirmRemove(false)}
              >
                Keep
              </Button>
            </>
          )}
          <Box sx={{ flex: 1 }} />
          {missing && !confirmRemove && (
            <Typography typography="s2" color="text.secondary" role="status">
              {missing}
            </Typography>
          )}
          {!confirmRemove && (
            <>
              <Button size="small" onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={busy || !complete || !canEdit}
                  onClick={() => finish(() => onSave(values))}
                >
                  {draft ? "Apply" : isNew ? "Add check" : "Save"}
                </Button>
              </GatewayAdminOnly>
              {allowTry && !draft && hasSample && (
                <GatewayAdminOnly allowed={canEdit}>
                  <Button
                    size="small"
                    variant="outlined"
                    disabled={busy || !complete || !canEdit || !enforced}
                    onClick={() =>
                      finish(() => onSave(values, { tryAfterSave: true }))
                    }
                  >
                    Save &amp; try
                  </Button>
                </GatewayAdminOnly>
              )}
            </>
          )}
        </Box>
      </Box>
    </GatewayDetailDrawer>
  );
}

RuleDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  meta: PropTypes.object,
  row: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  onRemove: PropTypes.func,
  // In the org-config editor: edits only the draft, so no activity or links.
  draft: PropTypes.bool,
  // The section is stored as a checks map: a check stored without an action
  // or a threshold opens on what the gateway runs it at (log, 0).
  asMap: PropTypes.bool,
  // Owner or Admin (useGatewayAdmin): only they may save or remove a rule.
  canEdit: PropTypes.bool,
  // False while the gateway runs no check (guardrails off in its config):
  // the drawer says so and promises no refusal.
  enforced: PropTypes.bool,
  // The host handles save-and-test navigation; draft hosts never opt in.
  allowTry: PropTypes.bool,
};
