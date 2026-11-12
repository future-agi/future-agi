import React, {
  useState,
  forwardRef,
  useImperativeHandle,
  useEffect,
  useMemo,
  useRef,
} from "react";
import PropTypes from "prop-types";
import { defineDataType, JsonViewer } from "@textea/json-viewer";
import { Box, Button, Stack, TextField, useTheme } from "@mui/material";
import { Icon } from "@iconify/react";

const MiniImageRender = ({ value }) => (
  <img
    width={150}
    src={value}
    alt="Base64 Preview"
    style={{ display: "inline-block" }}
  />
);

MiniImageRender.propTypes = {
  value: PropTypes.string,
};

const imageType = defineDataType({
  is: (value) => {
    return typeof value === "string" && value.startsWith("data:image");
  },
  Component: MiniImageRender,
});

const JsonCellEditor = forwardRef((props, ref) => {
  const theme = useTheme();
  // The editor holds the cell's own value. Plain values, text and arrays are
  // never wrapped, so both commit paths save the shape the cell already had.
  const initialValue = useMemo(() => {
    // Handle empty/null/undefined values
    if (
      props.value === undefined ||
      props.value === null ||
      props.value === ""
    ) {
      return {};
    }

    // If already an object, use directly
    if (typeof props.value === "object" && props.value !== null) {
      return props.value;
    }

    const strValue = String(props.value);

    // Try JSON parse
    try {
      return JSON.parse(strValue);
    } catch {
      // Not JSON: edit the raw text as a string
      return strValue;
    }
  }, [props.value]);

  const [value, setValue] = useState(initialValue);
  const [showAddField, setShowAddField] = useState(false);
  const [addPath, setAddPath] = useState([]);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const originalValueRef = useRef(JSON.stringify(initialValue));
  const [keyExistsError, setKeyExistsError] = useState(false);

  // Keep the empty-object editing affordance out of both commit paths.
  const hasDisplayRoot =
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 0;
  const displayValue = hasDisplayRoot ? { root: value } : value;
  const toValuePath = (path) =>
    hasDisplayRoot && path[0] === "root" ? path.slice(1) : path;

  const triggerValueChange = () => {
    const jsonString = JSON.stringify(value);

    if (
      jsonString !== originalValueRef.current &&
      props.colDef?.cellEditorParams?.onCellValueChanged
    ) {
      const params = {
        data: props.node.data,
        newValue: jsonString,
        oldValue: props.value,
        column: props.column,
        colDef: props.colDef,
        api: props.api,
        node: props.node,
        type: "cellValueChanged",
        source: "edit",
      };

      // Cancel AG Grid default onCellValueChanged call
      props.api.stopEditing(true);

      // Call your custom onCellValueChanged
      props.colDef.cellEditorParams.onCellValueChanged(params);
    } else {
      // Just cancel editing without changes
      props.api.stopEditing(true);
    }
  };

  useEffect(() => {
    const handleKeyDown = (e) => {
      // Enter belongs to the Add form while it is open; closing here would
      // drop the field being added.
      if (e.key === "Enter" && !e.shiftKey && !showAddField) {
        props.api.stopEditing();
      }

      // Add this condition to prevent Tab from closing editor when form is open
      if (e.key === "Tab" && showAddField) {
        e.preventDefault();
        e.stopPropagation();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [props?.api, showAddField]); // Add showAddField to dependencies

  useImperativeHandle(ref, () => ({
    getValue: () => {
      return JSON.stringify(value);
    },
    isPopup: () => true,
    isCancelBeforeStart: () => false,
    isCancelAfterEnd: () => false,
  }));

  const handleValueChange = (path, oldValue, newValue) => {
    const valuePath = toValuePath(path);
    if (valuePath.length === 0) {
      setValue(newValue);
      return;
    }

    setValue((prev) => {
      const newObj = Array.isArray(prev) ? [...prev] : { ...prev };
      let current = newObj;

      for (let i = 0; i < valuePath.length - 1; i++) {
        current = current[valuePath[i]];
      }

      current[valuePath[valuePath.length - 1]] = newValue;
      return newObj;
    });
  };

  const handleInlineAdd = (path) => {
    setAddPath(toValuePath(path));
    setShowAddField(true);
  };

  const handleAddFieldSubmit = () => {
    if (!newKey.trim()) return;

    const updated = JSON.parse(JSON.stringify(value));
    let target = updated;

    for (let i = 0; i < addPath.length; i++) {
      target = target[addPath[i]];
    }
    if (typeof target === "object" && target !== null && newKey in target) {
      setKeyExistsError(true);
      return;
    }

    if (Array.isArray(target)) {
      let parsedValue;
      try {
        parsedValue = JSON.parse(newValue);
      } catch {
        parsedValue = newValue;
      }
      target.push(parsedValue);
    } else if (typeof target === "object" && target !== null) {
      if (newKey in target) {
        return;
      }

      let parsedValue;
      try {
        parsedValue = JSON.parse(newValue);
      } catch {
        parsedValue = newValue;
      }

      target[newKey] = parsedValue;
    }

    setValue(updated);
    setShowAddField(false);
    setNewKey("");
    setNewValue("");
    setKeyExistsError(false);
  };

  // Enter in the Key or Value field adds the field. Handle it while the event
  // travels down, because the grid's popup editor ends editing on Enter
  // before React's bubbling handlers run.
  const handleAddFieldKeyDown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    e.stopPropagation();
    handleAddFieldSubmit();
  };

  const handleRemoveElement = (path) => {
    const valuePath = toValuePath(path);
    if (valuePath.length === 0) return;

    setValue((prev) => {
      const newObj = JSON.parse(JSON.stringify(prev));
      let current = newObj;

      for (let i = 0; i < valuePath.length - 1; i++) {
        current = current[valuePath[i]];
      }

      const keyToDelete = valuePath[valuePath.length - 1];

      if (Array.isArray(current)) {
        current.splice(keyToDelete, 1);
      } else {
        delete current[keyToDelete];
      }

      return newObj;
    });
  };

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        padding: "12px",
        overflow: "auto",
        minWidth: "250px",
        backgroundColor: "var(--bg-paper)",
        border: "3px solid var(--primary-main, #ac9cef)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {showAddField && (
        <div
          style={{
            position: "absolute",
            backgroundColor: "var(--bg-paper)",
            padding: "10px",
            border: "1px solid var(--border-default)",
            borderRadius: "4px",
            boxShadow: theme.customShadows?.dropdown,
            zIndex: 1000,
          }}
        >
          <div style={{ marginBottom: "8px" }}>
            <TextField
              label="Key"
              size="small"
              value={newKey}
              onChange={(e) => {
                setNewKey(e.target.value);
                setKeyExistsError(false); // Clear error on typing
              }}
              onKeyDownCapture={handleAddFieldKeyDown}
              error={keyExistsError}
              helperText={keyExistsError ? "Key already exists" : ""}
            />
          </div>
          <div style={{ marginBottom: "8px" }}>
            <TextField
              label="Value"
              size="small"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              onKeyDownCapture={handleAddFieldKeyDown}
            />
          </div>
          <div
            style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}
          >
            <Button
              size="small"
              onClick={() => {
                setShowAddField(false);
                setNewKey("");
                setNewValue("");
              }}
            >
              Cancel
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={handleAddFieldSubmit}
            >
              Add
            </Button>
          </div>
        </div>
      )}

      <Box
        sx={{
          resize: "both",
          overflow: "auto",
          minHeight: "180px",
          maxHeight: "500px",
          width: "412px",
          scrollbarWidth: "none",
          msOverflowStyle: "none",
          "&::-webkit-scrollbar": {
            display: "none",
          },
        }}
      >
        <JsonViewer
          value={displayValue}
          theme={theme.palette.mode}
          displayDataTypes={false}
          displaySize={false}
          indentWidth={2}
          rootName={false}
          editable={true}
          enableAdd={(path, currentValue) =>
            typeof currentValue === "object" && currentValue !== null
          }
          enableDelete={() => true}
          onDelete={handleRemoveElement}
          onAdd={handleInlineAdd}
          onChange={handleValueChange}
          valueTypes={[imageType]}
          sx={{
            fontSize: "13px",
            "& .jv-edit-input": {
              border: "1px solid var(--border-default)",
              borderRadius: "4px",
              padding: "2px 4px",
            },
          }}
        />
      </Box>

      <div style={{ marginTop: "12px", textAlign: "right" }}>
        <Stack
          direction="row"
          spacing={2}
          justifyContent="flex-start"
          sx={{ mt: 1.5 }}
        >
          <Button
            variant="outlined"
            size="small"
            color="primary"
            startIcon={
              <Icon
                icon="solar:check-linear"
                style={{
                  width: 16,
                  height: 16,
                  color: "inherit",
                }}
              />
            }
            onClick={triggerValueChange}
            sx={{
              p: theme.spacing(2),
              whiteSpace: "nowrap",
            }}
          >
            Apply Changes
          </Button>
        </Stack>
      </div>
    </div>
  );
});

JsonCellEditor.displayName = "JsonCellEditor";

JsonCellEditor.propTypes = {
  value: PropTypes.any,
  api: PropTypes.object,
  stopEditing: PropTypes.func,
  onCellValueChanged: PropTypes.func,
  node: PropTypes.object,
  column: PropTypes.object,
  colDef: PropTypes.object,
  data: PropTypes.object,
};

export default JsonCellEditor;
