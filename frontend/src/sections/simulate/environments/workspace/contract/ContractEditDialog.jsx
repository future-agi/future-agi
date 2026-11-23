import PropTypes from "prop-types";
import { useState } from "react";
import { Button, TextField } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import {
  CONTRACT_EDIT_COPY as E,
  CONTRACT_LISTS_COPY as L,
} from "./contract.constants";

const LIST_OF = { rule: L.rules, useCase: L.useCases };

export default function ContractEditDialog({ edit, onClose, onConfirm }) {
  const [text, setText] = useState(edit.op === "edit" ? edit.text : "");
  const list = LIST_OF[edit.target];
  const position = edit.index + 1;

  if (edit.op === "remove") {
    return (
      <ConfirmDialog
        open
        onClose={onClose}
        title={E.removeTitle(list.noun, list.id(position))}
        content={E.removeExplain(list.noun, edit.text)}
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => onConfirm(null)}
          >
            {E.removeAction(list.noun)}
          </Button>
        }
      />
    );
  }

  const next = text.trim();
  const isValid =
    next.length > 0 && (edit.op !== "edit" || next !== edit.text.trim());

  return (
    <ModalWrapper
      open
      onClose={onClose}
      title={
        edit.op === "add"
          ? E.addTitle(list.noun)
          : E.editTitle(list.noun, list.id(position))
      }
      subTitle={E.explain}
      actionBtnTitle={E.submit}
      isValid={isValid}
      onSubmit={() => isValid && onConfirm(next)}
    >
      <TextField
        autoFocus
        fullWidth
        multiline
        minRows={2}
        size="small"
        label={list.field}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </ModalWrapper>
  );
}

ContractEditDialog.propTypes = {
  edit: PropTypes.shape({
    target: PropTypes.oneOf(["rule", "useCase"]).isRequired,
    op: PropTypes.oneOf(["add", "edit", "remove"]).isRequired,
    index: PropTypes.number,
    text: PropTypes.string,
  }).isRequired,
  onClose: PropTypes.func.isRequired,
  onConfirm: PropTypes.func.isRequired,
};
