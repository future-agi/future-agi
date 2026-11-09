import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { FieldTag } from "src/sections/common/ValueViewer/FieldCard";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import { withKeyHint } from "src/utils/keymap";
import FieldEditor from "./FieldEditor";

const EditButton = ({ label, onClick }) => (
  <CustomTooltip
    show
    size="small"
    placement="top"
    arrow
    title={withKeyHint("Edit", "edit")}
  >
    <IconButton
      size="small"
      aria-label={`Edit ${label}`}
      onClick={onClick}
      sx={{ p: 0.5, color: "text.secondary" }}
    >
      <Iconify icon="mdi:pencil-outline" width={15} />
    </IconButton>
  </CustomTooltip>
);

EditButton.propTypes = { label: PropTypes.string, onClick: PropTypes.func };

const KvRow = ({ field, mode, edit }) => {
  const { column, value, edited } = field;
  const editing = edit.editingId === column.id;
  return (
    <Box
      sx={{
        borderTop: "1px solid",
        borderColor: "divider",
        borderLeft: "3px solid",
        borderLeftColor: edited ? "amber.500" : "transparent",
        "&:first-of-type": { borderTop: 0 },
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        gap={1}
        sx={{ px: 1, py: 0.625 }}
      >
        <Typography
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ width: 150, flexShrink: 0, pt: 0.25 }}
          title={column.name}
        >
          {column.name}
        </Typography>
        <Box sx={{ flex: 1, minWidth: 0, typography: "s1" }}>
          {!editing && (
            <ValueViewer
              value={value}
              dataType={column.data_type}
              mode={mode}
              clampChars={300}
              // IT-R2 G5 P5: paths under the column (metadata.customer.tier).
              fieldKey={column.name}
              basePath={column.name}
            />
          )}
        </Box>
        {edited && <FieldTag tone="amber">Edited</FieldTag>}
        {edited && (
          <Button
            size="small"
            onClick={() => edit.revert(column.id)}
            sx={{ minWidth: 0, px: 0.5, py: 0, typography: "s2" }}
          >
            Revert
          </Button>
        )}
        {edit.canEdit && !editing && (
          <EditButton
            label={column.name}
            onClick={() => edit.start(column.id)}
          />
        )}
      </Stack>
      {editing && (
        <Box sx={{ px: 1, pb: 1 }}>
          <FieldEditor
            dataType={column.data_type}
            value={value}
            applyRef={edit.applyRef}
            onCancel={edit.cancel}
            onApply={(v) => edit.apply(column.id, v)}
          />
        </Box>
      )}
    </Box>
  );
};

KvRow.propTypes = {
  field: PropTypes.object.isRequired,
  mode: PropTypes.string,
  edit: PropTypes.object.isRequired,
};

/**
 * Metadata (PRODUCT-SPEC §3.4 section 4): metadata-role columns as a
 * key/value table, plus the row's own `Row metadata` JSON (collapsed).
 */
const MetadataSection = ({
  fields,
  rowMeta,
  mode,
  edit,
  metaKey,
  canEditRowMeta = true,
}) => {
  const [jsonOpen, setJsonOpen] = useState(false);
  const editingJson = edit.editingId === metaKey;
  const jsonEdited = Boolean(rowMeta.edited);
  return (
    <Stack gap={1}>
      {fields.length > 0 && (
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
          }}
        >
          {fields.map((f) => (
            <KvRow key={f.column.id} field={f} mode={mode} edit={edit} />
          ))}
        </Box>
      )}
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderLeft: jsonEdited ? "3px solid" : "1px solid",
          borderLeftColor: jsonEdited ? "amber.500" : "divider",
          borderRadius: 1,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ px: 1, py: 0.5 }}
        >
          <ButtonBase
            onClick={() => setJsonOpen((o) => !o)}
            aria-expanded={jsonOpen || editingJson}
            sx={{ gap: 0.5, borderRadius: 0.5 }}
          >
            <Iconify
              icon={
                jsonOpen || editingJson
                  ? "eva:chevron-down-fill"
                  : "eva:chevron-right-fill"
              }
              width={16}
              sx={{ color: "text.disabled" }}
            />
            <Iconify
              icon="material-symbols:data-object"
              width={16}
              sx={{ color: "text.secondary" }}
            />
            {/* IT-R2 G5 P6: one Metadata section; the row's own JSON reads
                as `Other metadata · 1 key` under the column fields. */}
            <Typography typography="s2" fontWeight="fontWeightMedium">
              Other metadata
            </Typography>
            <Typography typography="s2" color="text.disabled">
              {(() => {
                const k = Object.keys(rowMeta.value ?? {}).length;
                return `${k} ${k === 1 ? "key" : "keys"}`;
              })()}
            </Typography>
          </ButtonBase>
          {jsonEdited && <FieldTag tone="amber">Edited</FieldTag>}
          <Box sx={{ flex: 1 }} />
          {jsonEdited && (
            <Button
              size="small"
              onClick={() => edit.revert(metaKey)}
              sx={{ minWidth: 0, px: 0.5, py: 0, typography: "s2" }}
            >
              Revert
            </Button>
          )}
          {edit.canEdit && canEditRowMeta && !editingJson && (
            <EditButton
              label="Other metadata"
              onClick={() => {
                setJsonOpen(true);
                edit.start(metaKey);
              }}
            />
          )}
        </Stack>
        {(jsonOpen || editingJson) && (
          <Box sx={{ px: 1, pb: 1 }}>
            {editingJson ? (
              <FieldEditor
                dataType="json"
                value={JSON.stringify(rowMeta.value ?? {})}
                applyRef={edit.applyRef}
                onCancel={edit.cancel}
                onApply={(v) => edit.apply(metaKey, v)}
              />
            ) : (
              <Box
                sx={{
                  px: 1.25,
                  py: 1,
                  borderRadius: 1,
                  bgcolor: "background.neutral",
                }}
              >
                <ValueViewer
                  value={rowMeta.value ?? {}}
                  dataType="json"
                  mode={mode}
                />
              </Box>
            )}
          </Box>
        )}
      </Box>
    </Stack>
  );
};

MetadataSection.propTypes = {
  fields: PropTypes.array.isRequired,
  rowMeta: PropTypes.shape({ value: PropTypes.object, edited: PropTypes.bool })
    .isRequired,
  mode: PropTypes.string,
  edit: PropTypes.object.isRequired,
  metaKey: PropTypes.string.isRequired,
  canEditRowMeta: PropTypes.bool,
};

export default MetadataSection;
