import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { IconButton, InputAdornment, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { useDebounce } from "src/hooks/use-debounce";
import { getUtf8ByteLength } from "src/api/contracts/filter-contract";
import { INPUT_OUTPUT_SEARCH_MAX_BYTES } from "./inputOutputSearch";

// External filter restoration cancels a pending draft. A successful search
// commit must not remount the input and steal focus while the user is typing.
export default function InputOutputSearch({ value, onChange }) {
  const [draft, setDraft] = useState(value);
  const dirty = useRef(false);
  const settledDraft = useDebounce(draft, 300);
  const tooLong = getUtf8ByteLength(draft) > INPUT_OUTPUT_SEARCH_MAX_BYTES;

  useEffect(() => {
    dirty.current = false;
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (!dirty.current || tooLong || settledDraft !== draft) return;
    dirty.current = false;
    onChange(draft.trim() ? draft : "");
  }, [draft, settledDraft, tooLong, onChange]);

  return (
    <TextField
      size="small"
      label="Search input or output"
      value={draft}
      onChange={(event) => {
        dirty.current = true;
        setDraft(event.target.value);
      }}
      error={tooLong}
      helperText={tooLong ? "Use 1,024 UTF-8 bytes or fewer." : undefined}
      sx={{ width: 240, maxWidth: "100%", flexShrink: 1 }}
      InputProps={{
        endAdornment: draft ? (
          <InputAdornment position="end">
            <IconButton
              size="small"
              aria-label="Clear input or output search"
              onClick={() => {
                dirty.current = false;
                setDraft("");
                onChange("");
              }}
            >
              <Iconify icon="mingcute:close-line" width={14} />
            </IconButton>
          </InputAdornment>
        ) : undefined,
      }}
    />
  );
}

InputOutputSearch.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};
