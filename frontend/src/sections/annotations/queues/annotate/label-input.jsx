import PropTypes from "prop-types";
import {
  useRef,
  useState,
  useEffect,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";
import {
  Box,
  Checkbox,
  Radio,
  Slider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

const LABEL_COLORS = {
  star: "#d26464",
  categorical: "#2e9469",
  numeric: "#5278c7",
  text: "#8067c9",
  thumbs_up_down: "#b7772a",
};

const Kbd = ({ children }) => (
  <Box
    component="kbd"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      minWidth: 18,
      height: 18,
      px: 0.5,
      borderRadius: 0.5,
      bgcolor: "action.hover",
      border: "1px solid",
      borderColor: "divider",
      fontSize: 10,
      fontWeight: 600,
      fontFamily: "inherit",
      color: "text.secondary",
      lineHeight: 1,
    }}
  >
    {children}
  </Box>
);

export default function LabelInput({
  label,
  value,
  onChange,
  index,
  focused,
  hasError,
  textFlushRef,
  labelNotes,
  onLabelNotesChange,
}) {
  const { type, settings = {} } = label;
  const color = LABEL_COLORS[type] || "#888";
  const inputRef = useRef(null);

  return (
    <Box
      sx={{
        borderRadius: 0.5,
        bgcolor: (theme) =>
          alpha(color, theme.palette.mode === "dark" ? 0.07 : 0.045),
        border: "1px solid",
        borderColor: (theme) =>
          hasError
            ? alpha(theme.palette.error.main, 0.55)
            : focused
              ? alpha(color, 0.5)
              : alpha(theme.palette.text.primary, 0.06),
        transition: "border-color 0.15s, box-shadow 0.15s",
        boxShadow: (theme) =>
          hasError
            ? `0 0 0 2px ${alpha(theme.palette.error.main, 0.16)}`
            : focused
              ? `0 0 0 2px ${alpha(color, theme.palette.mode === "dark" ? 0.18 : 0.12)}`
              : "none",
        overflow: "hidden",
      }}
    >
      {/* Label header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.5,
          pt: 1.25,
          pb: 0.5,
        }}
      >
        <Box
          sx={{
            width: 3,
            height: 14,
            borderRadius: 0.5,
            bgcolor: hasError ? "error.main" : color,
            flexShrink: 0,
            transition: "bgcolor 0.15s",
          }}
        />
        <Typography
          variant="body2"
          fontWeight={600}
          color={hasError ? "error.main" : "text.primary"}
          sx={{ flex: 1, lineHeight: 1.3, transition: "color 0.15s" }}
        >
          {label.name}
          {label.required && (
            <Typography component="span" color="error.main" sx={{ ml: 0.25 }}>
              *
            </Typography>
          )}
          {hasError && (
            <Typography
              component="span"
              color="error.main"
              sx={{ ml: 0.5, fontWeight: 500 }}
            >
              (required)
            </Typography>
          )}
        </Typography>
        {typeof index === "number" && (
          <Box
            sx={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              minWidth: 24,
              height: 24,
              borderRadius: "50%",
              border: "1px solid",
              borderColor: (theme) => alpha(theme.palette.text.primary, 0.16),
              bgcolor: (theme) => alpha(theme.palette.text.primary, 0.045),
              color: "text.secondary",
              fontSize: 12,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {index + 1}
          </Box>
        )}
      </Box>

      {label.description && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ px: 1.5, pb: 0.5, display: "block", lineHeight: 1.3 }}
        >
          {label.description}
        </Typography>
      )}

      {/* Input area */}
      <Box sx={{ px: 1.5, pb: 1.5, pt: 0.5 }}>
        {type === "star" && (
          <StarInput
            value={value}
            settings={settings}
            onChange={onChange}
            focused={focused}
          />
        )}

        {type === "categorical" && (
          <CategoricalInput
            settings={settings}
            value={value?.selected || []}
            onChange={(selected) => onChange({ selected })}
            focused={focused}
          />
        )}

        {type === "numeric" && (
          <NumericInput
            settings={settings}
            value={value?.value ?? null}
            onChange={(val) => onChange({ value: val })}
            inputRef={inputRef}
          />
        )}

        {type === "text" && (
          <DebouncedTextInput
            ref={textFlushRef}
            inputRef={inputRef}
            value={value?.text || ""}
            onChange={(text) => onChange({ text })}
            placeholder={settings.placeholder || "Enter text..."}
            minLength={settings.min_length || undefined}
            maxLength={settings.max_length || undefined}
          />
        )}

        {type === "thumbs_up_down" && (
          <ThumbsInput value={value} onChange={onChange} focused={focused} />
        )}
      </Box>

      {label.allow_notes && (
        <Box sx={{ px: 1.5, pb: 1.5 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 0.5, display: "block" }}
          >
            Notes (optional)
          </Typography>
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={2}
            maxRows={4}
            placeholder="Add notes for this label..."
            value={labelNotes || ""}
            onChange={(e) => onLabelNotesChange?.(e.target.value)}
          />
        </Box>
      )}
    </Box>
  );
}

Kbd.propTypes = {
  children: PropTypes.node,
};

LabelInput.propTypes = {
  label: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  index: PropTypes.number,
  focused: PropTypes.bool,
  hasError: PropTypes.bool,
  textFlushRef: PropTypes.any,
  labelNotes: PropTypes.string,
  onLabelNotesChange: PropTypes.func,
};

// ---------------------------------------------------------------------------
// Debounced text input — keeps local state and debounces parent onChange (300ms).
// Exposes flush() via ref so parent can synchronously push latest value before submit.
// ---------------------------------------------------------------------------
const DebouncedTextInput = forwardRef(function DebouncedTextInput(
  { inputRef, value, onChange, placeholder, minLength, maxLength },
  ref,
) {
  const [localValue, setLocalValue] = useState(value);
  const timerRef = useRef(null);
  const localRef = useRef(value);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Sync from parent when value changes externally (e.g. annotation reload)
  useEffect(() => {
    setLocalValue(value);
    localRef.current = value;
  }, [value]);

  const handleChange = useCallback((e) => {
    const text = e.target.value;
    setLocalValue(text);
    localRef.current = text;
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onChangeRef.current(text);
    }, 300);
  }, []);

  // Expose flush so parent can push latest value synchronously before submit
  useImperativeHandle(ref, () => ({
    flush() {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        onChangeRef.current(localRef.current);
      }
    },
  }));

  // Clear timer on unmount
  useEffect(() => () => clearTimeout(timerRef.current), []);

  return (
    <TextField
      inputRef={inputRef}
      multiline
      minRows={1}
      maxRows={4}
      fullWidth
      size="small"
      placeholder={placeholder}
      value={localValue}
      onChange={handleChange}
      inputProps={{ minLength, maxLength }}
      sx={{
        "& .MuiOutlinedInput-root": {
          bgcolor: "background.paper",
          fontSize: 13,
        },
      }}
    />
  );
});

DebouncedTextInput.propTypes = {
  inputRef: PropTypes.any,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  placeholder: PropTypes.string,
  minLength: PropTypes.number,
  maxLength: PropTypes.number,
};

// ---------------------------------------------------------------------------
// Star rating with number key hints
// ---------------------------------------------------------------------------
function StarInput({ value, settings, onChange, focused }) {
  const max = settings.no_of_stars || 5;
  const current = value?.rating || 0;

  return (
    <Stack direction="row" spacing={0.25} alignItems="center">
      {Array.from({ length: max }, (_, i) => {
        const starVal = i + 1;
        const isActive = starVal <= current;
        return (
          <Box
            key={starVal}
            onClick={() =>
              onChange({ rating: starVal === current ? 0 : starVal })
            }
            sx={{
              position: "relative",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 32,
              height: 32,
              borderRadius: 0.5,
              transition: "all 0.1s",
              bgcolor: isActive ? "rgba(239,68,68,0.1)" : "transparent",
              "&:hover": {
                bgcolor: "rgba(239,68,68,0.15)",
              },
            }}
          >
            <Iconify
              icon={isActive ? "solar:star-bold" : "solar:star-line-duotone"}
              width={20}
              sx={{
                color: isActive ? "#ef4444" : "text.disabled",
                transition: "color 0.1s",
              }}
            />
            {focused && (
              <Box
                sx={{
                  position: "absolute",
                  bottom: -1,
                  fontSize: 8,
                  fontWeight: 700,
                  color: "text.disabled",
                  lineHeight: 1,
                }}
              >
                {starVal}
              </Box>
            )}
          </Box>
        );
      })}
    </Stack>
  );
}

StarInput.propTypes = {
  value: PropTypes.any,
  settings: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  focused: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Thumbs up / down with shortcut hints
// ---------------------------------------------------------------------------
function ThumbsInput({ value, onChange, focused }) {
  const isUp = value?.value === "up";
  const isDown = value?.value === "down";

  return (
    <Stack direction="row" spacing={1}>
      <Box
        onClick={() => onChange({ value: isUp ? null : "up" })}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.5,
          py: 0.75,
          borderRadius: 0.5,
          cursor: "pointer",
          border: "1px solid",
          borderColor: isUp ? "#22c55e" : "divider",
          bgcolor: isUp ? "rgba(34,197,94,0.08)" : "transparent",
          transition: "all 0.15s",
          "&:hover": {
            borderColor: "#22c55e",
            bgcolor: "rgba(34,197,94,0.05)",
          },
        }}
      >
        <Iconify
          icon="solar:like-bold"
          width={18}
          sx={{ color: isUp ? "#22c55e" : "text.secondary" }}
        />
        <Typography
          variant="caption"
          fontWeight={500}
          color={isUp ? "#22c55e" : "text.secondary"}
        >
          Yes
        </Typography>
        {focused && <Kbd>1</Kbd>}
      </Box>
      <Box
        onClick={() => onChange({ value: isDown ? null : "down" })}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          px: 1.5,
          py: 0.75,
          borderRadius: 0.5,
          cursor: "pointer",
          border: "1px solid",
          borderColor: isDown ? "#ef4444" : "divider",
          bgcolor: isDown ? "rgba(239,68,68,0.08)" : "transparent",
          transition: "all 0.15s",
          "&:hover": {
            borderColor: "#ef4444",
            bgcolor: "rgba(239,68,68,0.05)",
          },
        }}
      >
        <Iconify
          icon="solar:dislike-bold"
          width={18}
          sx={{ color: isDown ? "#ef4444" : "text.secondary" }}
        />
        <Typography
          variant="caption"
          fontWeight={500}
          color={isDown ? "#ef4444" : "text.secondary"}
        >
          No
        </Typography>
        {focused && <Kbd>2</Kbd>}
      </Box>
    </Stack>
  );
}

ThumbsInput.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  focused: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Categorical
// ---------------------------------------------------------------------------
function getOptionLabel(opt) {
  if (typeof opt === "string") return opt;
  return opt?.label || opt?.value || "";
}

function CategoricalInput({ settings, value, onChange, focused }) {
  const rawOptions = settings.options || [];
  const options = rawOptions.map(getOptionLabel).filter(Boolean);
  const isMulti = settings.multi_choice || false;

  if (isMulti) {
    return (
      <Stack spacing={0.25}>
        {options.map((opt, i) => (
          <Box
            key={opt}
            onClick={() => {
              const next = value.includes(opt)
                ? value.filter((v) => v !== opt)
                : [...value, opt];
              onChange(next);
            }}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
              px: 0,
              py: 0.5,
              borderRadius: 0.5,
              cursor: "pointer",
              bgcolor: value.includes(opt)
                ? "rgba(34,197,94,0.08)"
                : "transparent",
              "&:hover": { bgcolor: "action.hover" },
              transition: "background-color 0.1s",
            }}
          >
            <Box
              sx={{ display: "flex", alignItems: "center", gap: 0.75, flex: 1 }}
            >
              <Checkbox
                checked={value.includes(opt)}
                size="small"
                sx={{
                  p: 0,
                  color: "text.disabled",
                  "&.Mui-checked": { color: "#22c55e" },
                }}
              />
              <Typography variant="body2">{opt}</Typography>
            </Box>
            {focused && i < 9 && <Kbd>{i + 1}</Kbd>}
          </Box>
        ))}
      </Stack>
    );
  }

  return (
    <Stack spacing={0.25}>
      {options.map((opt, i) => {
        const isSelected = value[0] === opt;
        return (
          <Box
            key={opt}
            // Clicking an already-selected option deselects it (sends empty array) — intentional
            onClick={() => onChange(isSelected ? [] : [opt])}
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
              px: 0.3,
              py: 0.5,
              borderRadius: 0.5,
              cursor: "pointer",
              bgcolor: isSelected ? "rgba(34,197,94,0.08)" : "transparent",
              "&:hover": { bgcolor: "action.hover" },
              transition: "background-color 0.1s",
            }}
          >
            <Box
              sx={{ display: "flex", alignItems: "center", gap: 0.75, flex: 1 }}
            >
              <Radio
                checked={isSelected}
                size="small"
                sx={{
                  p: 0,
                  color: "text.disabled",
                  "&.Mui-checked": { color: "#22c55e" },
                }}
              />
              <Typography variant="body2">{opt}</Typography>
            </Box>
            {focused && i < 9 && <Kbd>{i + 1}</Kbd>}
          </Box>
        );
      })}
    </Stack>
  );
}

CategoricalInput.propTypes = {
  settings: PropTypes.object,
  value: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  focused: PropTypes.bool,
};

// ---------------------------------------------------------------------------
// Numeric
// ---------------------------------------------------------------------------
function NumericInput({ settings, value, onChange, inputRef }) {
  const min = settings.min ?? 0;
  const max = settings.max ?? 10;
  const step = settings.step ?? 1;

  return (
    <Stack direction="row" spacing={2} alignItems="center">
      <Slider
        value={value ?? min}
        min={min}
        max={max}
        step={step}
        onChange={(_, v) => onChange(v)}
        sx={{ flex: 1, color: "#3b82f6" }}
      />
      <TextField
        inputRef={inputRef}
        type="number"
        size="small"
        value={value ?? ""}
        onChange={(e) => {
          const n = e.target.value === "" ? null : Number(e.target.value);
          if (n === null || !Number.isNaN(n)) {
            onChange(n === null ? n : Math.max(min, Math.min(max, n)));
          }
        }}
        inputProps={{ min, max, step }}
        sx={{
          width: 64,
          "& .MuiOutlinedInput-root": { fontSize: 13, color: "text.primary" },
          "& input": { textAlign: "center", px: 0.5 },
        }}
      />
    </Stack>
  );
}

NumericInput.propTypes = {
  settings: PropTypes.object,
  value: PropTypes.number,
  onChange: PropTypes.func.isRequired,
  inputRef: PropTypes.any,
};
