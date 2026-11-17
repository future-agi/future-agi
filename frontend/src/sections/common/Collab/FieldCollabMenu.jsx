// HO D1 / D2 · a Row view field's ⋯ menu: `Translate to…` (the languages the
// translate read offers for this field on this row; absent when none) and
// `Comment on this field` (Activity › Comments with the composer on it).
// `autoOpen` (the grid's `Translate cell…` deep link) opens Translate to…
// once, or says why there is nothing to translate. The translation is
// handed to the parent (`onTranslated`), which shows it under the card.
// Where the deployment does not offer translation, `Translate to…` stays,
// turned off, with the shared line under the label; nothing is read or sent,
// and the deep link says the shared line.
import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  IconButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Menu,
  MenuItem,
} from "@mui/material";
import { useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import { notify } from "src/sections/common/ux/toast";
import {
  collabError,
  useTranslate,
  useTranslateInfo,
} from "src/api/develop/row-collab";
import {
  fieldCommentDeepLink,
  fieldTargets,
  noTargetText,
  withoutTranslateParam,
} from "./fieldCollabModel";
import { notOfferedLine } from "./notOffered";

export default function FieldCollabMenu({
  datasetId,
  rowId,
  columnId,
  name,
  rowLabel,
  autoOpen = false,
  onTranslated,
  canComment = true,
  canTranslate = true,
}) {
  const [, setSearchParams] = useSearchParams();
  const btn = useRef(null);
  const [anchor, setAnchor] = useState(null);
  const [level, setLevel] = useState("root");
  const offId = useId();
  // Translate applies to this field but this deployment does not offer it.
  const translateOff = canTranslate
    ? notOfferedLine("dataset.collab.translate")
    : null;
  // integration review: a row not in the latest version is not asked about.
  const info = useTranslateInfo(
    datasetId,
    rowId,
    canTranslate && !translateOff ? {} : { enabled: false },
  );
  const translate = useTranslate(datasetId);
  const targets = translateOff ? [] : fieldTargets(info.data, name);

  // D2: the grid's deep link opens Translate to… once the read has come.
  const handled = useRef(false);
  useEffect(() => {
    if (!autoOpen || handled.current) return;
    if (translateOff) {
      handled.current = true;
      setSearchParams((p) => withoutTranslateParam(p), { replace: true });
      notify(translateOff, { variant: "info" });
      return;
    }
    if (!info.data) return;
    handled.current = true;
    setSearchParams((p) => withoutTranslateParam(p), { replace: true });
    if (targets.length) {
      btn.current?.scrollIntoView?.({ block: "center" });
      setLevel("targets");
      setAnchor(btn.current);
    } else notify(noTargetText(info.data, name, rowLabel), { variant: "info" });
  }, [autoOpen, info.data, translateOff]); // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    setAnchor(null);
    setLevel("root");
  };
  const pick = async (t) => {
    close();
    try {
      const res = await translate.mutateAsync({
        row_id: rowId,
        column_ids: [columnId],
        target: t.code,
      });
      onTranslated(res);
    } catch (e) {
      notify(collabError(e, `Couldn't translate ${name}.`), {
        variant: "error",
      });
    }
  };

  // integration review: nothing to offer (no translation, no comment, e.g. a
  // field read as of an older version): no ⋯ at all, never an empty menu.
  // The deep link above still says why there is nothing to translate.
  if (!targets.length && !canComment && !translateOff) return null;

  return (
    <>
      <IconButton
        ref={btn}
        size="small"
        aria-label={`More actions for ${name}`}
        data-testid={`field-more-${name}`}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ p: 0.5, color: "text.secondary" }}
      >
        <Iconify icon="mdi:dots-horizontal" width={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={close}
        MenuListProps={{ dense: true, "data-testid": "field-more-menu" }}
      >
        {level === "targets"
          ? [
              <ListSubheader
                key="h"
                sx={{ typography: "s3", lineHeight: "28px" }}
              >
                {`Translate ${name} to`}
              </ListSubheader>,
              ...targets.map((t) => (
                <MenuItem
                  key={t.code}
                  data-testid={`field-translate-to-${t.code}`}
                  onClick={() => pick(t)}
                >
                  {t.name}
                </MenuItem>
              )),
            ]
          : [
              translateOff ? (
                <MenuItem
                  key="translate"
                  data-testid="field-translate"
                  disabled
                  aria-label="Translate to…"
                  aria-describedby={offId}
                  sx={{
                    alignItems: "flex-start",
                    whiteSpace: "normal",
                    maxWidth: 280,
                    "&.Mui-disabled": { opacity: 1 },
                  }}
                >
                  <ListItemIcon sx={{ mt: 0.25, opacity: 0.6 }}>
                    <Iconify icon="mdi:translate" width={16} />
                  </ListItemIcon>
                  <ListItemText
                    sx={{ my: 0 }}
                    primary="Translate to…"
                    secondary={translateOff}
                    primaryTypographyProps={{ color: "text.secondary" }}
                    secondaryTypographyProps={{
                      id: offId,
                      typography: "s3",
                      color: "text.secondary",
                    }}
                  />
                </MenuItem>
              ) : targets.length ? (
                <MenuItem
                  key="translate"
                  data-testid="field-translate"
                  disabled={translate.isPending}
                  onClick={() => setLevel("targets")}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:translate" width={16} />
                  </ListItemIcon>
                  Translate to…
                </MenuItem>
              ) : null,
              // integration: no comment on a field read as of an older version.
              canComment ? (
                <MenuItem
                  key="comment"
                  data-testid="field-comment"
                  onClick={() => {
                    close();
                    setSearchParams((p) =>
                      fieldCommentDeepLink(p, rowId, columnId),
                    );
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:comment-outline" width={16} />
                  </ListItemIcon>
                  Comment on this field
                </MenuItem>
              ) : null,
            ]}
      </Menu>
    </>
  );
}

FieldCollabMenu.propTypes = {
  datasetId: PropTypes.string.isRequired,
  rowId: PropTypes.string.isRequired,
  columnId: PropTypes.string.isRequired,
  name: PropTypes.string.isRequired,
  rowLabel: PropTypes.string,
  autoOpen: PropTypes.bool,
  onTranslated: PropTypes.func.isRequired,
  canComment: PropTypes.bool,
  canTranslate: PropTypes.bool,
};
