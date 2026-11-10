import React, {
  useState,
  useMemo,
  useRef,
  useCallback,
  useLayoutEffect,
} from "react";
import {
  Box,
  Divider,
  Chip,
  InputAdornment,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  MenuList,
  Popover,
  Skeleton,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { Controller } from "react-hook-form";
import PropTypes from "prop-types";
import _ from "lodash";
import { useNavigate } from "react-router";
import SvgColor from "src/components/svg-color";
import Iconify from "src/components/iconify";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { AGENT_TYPES } from "../agents/constants";
import { SourceType, getIconForAgentDefinitions } from "./common";

// ---------------------------------------------------------------------

const CATEGORIES = [
  { label: "All", value: "all", iconifyIcon: "eva:grid-fill" },
  {
    label: "Voice agent definition",
    value: "voice_agent",
    svgSrc: "/assets/icons/ic_call_inbound.svg",
  },
  {
    label: "Chat agent definition",
    value: "chat_agent",
    svgSrc: "/assets/icons/ic_chat_single.svg",
  },
  {
    label: "Prompts",
    value: "prompts",
    svgSrc: "/assets/icons/navbar/ic_prompt.svg",
  },
];

const getOptionIcon = (option) => {
  if (option?.sourceType === SourceType.PROMPT) {
    return "/assets/icons/navbar/ic_prompt.svg";
  }
  return getIconForAgentDefinitions(option?.agentType);
};

// ---------------------------------------------------------------------

const CategorizedSourceDropdownInner = React.forwardRef(
  (
    {
      value,
      onChange,
      onBlur,
      options = [],
      error,
      helperText,
      label,
      placeholder = "Select agent definition or prompt",
      required,
      disabled,
      onScrollEnd,
      isFetchingNextPage,
      size = "small",
      fullWidth,
      sx,
    },
    _ref,
  ) => {
    const theme = useTheme();
    const navigate = useNavigate();
    const [openDropdown, setOpenDropdown] = useState(false);
    const [searchedValue, setSearchedValue] = useState("");
    const [selectedCategory, setSelectedCategory] = useState("all");
    const [position, setPosition] = useState("bottom");
    const containerRef = useRef(null);
    const inputRef = useRef(null);
    const [textFieldWidth, setTextFieldWidth] = useState(0);

    const scrollRef = useScrollEnd(onScrollEnd, [isFetchingNextPage]);

    useLayoutEffect(() => {
      if (containerRef.current) {
        const updateWidth = () => {
          const width = containerRef.current.getBoundingClientRect().width;
          setTextFieldWidth(width);
        };
        updateWidth();
        window.addEventListener("resize", updateWidth);
        return () => window.removeEventListener("resize", updateWidth);
      }
    }, []);

    const onClose = useCallback(() => {
      setOpenDropdown(false);
      setSearchedValue("");
    }, []);

    const handleOpen = useCallback(() => {
      if (disabled) return;
      setOpenDropdown(true);
      setSearchedValue("");

      const boxRect = containerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - boxRect.bottom;
      const spaceAbove = boxRect.top;

      if (spaceBelow < 350 && spaceAbove > spaceBelow) {
        setPosition("top");
      } else {
        setPosition("bottom");
      }
    }, [disabled]);

    const handleOnFocus = useCallback(() => {
      handleOpen();
    }, [handleOpen]);

    const handleDropdownIconClick = useCallback(
      (e) => {
        e.stopPropagation();
        if (disabled) return;
        if (openDropdown) {
          onClose();
        } else {
          handleOpen();
          inputRef.current?.focus();
        }
      },
      [handleOpen, openDropdown, disabled, onClose],
    );

    const handleItemClick = useCallback(
      (e, option) => {
        const event = {
          ...e,
          target: { ...e.target, value: option.value, option },
        };
        setSearchedValue("");
        onChange(event);
        onClose();
      },
      [onChange, onClose],
    );

    const selectedOption = useMemo(
      () => options?.find((opt) => opt.value === value),
      [options, value],
    );

    const displayValue = useMemo(() => {
      if (openDropdown) return searchedValue;
      return selectedOption?.label || "";
    }, [openDropdown, searchedValue, selectedOption]);

    const filteredOptions = useMemo(() => {
      let filtered = options;

      if (selectedCategory === "voice_agent") {
        filtered = filtered.filter(
          (opt) =>
            opt.sourceType === SourceType.AGENT_DEFINITION &&
            opt.agentType === AGENT_TYPES.VOICE,
        );
      } else if (selectedCategory === "chat_agent") {
        filtered = filtered.filter(
          (opt) =>
            opt.sourceType === SourceType.AGENT_DEFINITION &&
            opt.agentType === AGENT_TYPES.CHAT,
        );
      } else if (selectedCategory === "prompts") {
        filtered = filtered.filter(
          (opt) => opt.sourceType === SourceType.PROMPT,
        );
      }

      if (searchedValue) {
        filtered = filtered.filter((opt) =>
          opt.label?.toLowerCase().includes(searchedValue.toLowerCase()),
        );
      }

      return filtered;
    }, [options, selectedCategory, searchedValue]);

    const startAdornmentIcon =
      selectedOption && !openDropdown ? getOptionIcon(selectedOption) : null;

    return (
      <>
        <TextField
          ref={containerRef}
          inputRef={inputRef}
          autoComplete="off"
          type="text"
          label={label}
          placeholder={placeholder}
          onChange={(e) => setSearchedValue(e.target.value)}
          onFocus={handleOnFocus}
          onClick={handleOpen}
          onBlur={onBlur}
          value={displayValue}
          size={size}
          fullWidth={fullWidth}
          required={required}
          error={error}
          helperText={helperText}
          disabled={disabled}
          sx={{
            "& .MuiOutlinedInput-root": {
              "& fieldset": {
                borderColor: "divider",
                borderBottomLeftRadius: "4px",
                borderBottomRightRadius: "4px",
              },
              "&:hover fieldset": {
                borderColor: "divider",
              },
              "&.Mui-focused fieldset": {
                borderColor: "divider",
                borderBottomLeftRadius:
                  position === "bottom" ? 0 : theme.spacing(0.5),
                borderBottomRightRadius:
                  position === "bottom" ? 0 : theme.spacing(0.5),
                borderTopLeftRadius:
                  position === "top" ? 0 : theme.spacing(0.5),
                borderTopRightRadius:
                  position === "top" ? 0 : theme.spacing(0.5),
              },
            },
            "& .MuiOutlinedInput-notchedOutline legend": {
              width:
                (label?.length || 0) > 7
                  ? `${(label?.length || 0) - 1}ch`
                  : `${label?.length || 0}ch`,
            },
            input: { color: "text.secondary" },
            "& .MuiInputBase-input::placeholder": {
              color: "text.disabled",
              opacity: 0.7,
            },
            "& .MuiFormLabel-asterisk": {
              color: (t) => t.palette.error.main,
            },
            "& .MuiOutlinedInput-root:hover .MuiOutlinedInput-notchedOutline": {
              borderColor: error ? "red.500" : "action.hover",
            },
            "& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline":
              {
                borderColor: error ? "red.500" : "action.hover",
              },
            ...sx,
          }}
          InputLabelProps={{
            shrink: true,
            style: {
              paddingLeft: 1,
              paddingRight: 2,
              background: "var(--bg-paper)",
            },
          }}
          InputProps={{
            startAdornment: startAdornmentIcon ? (
              <InputAdornment position="start">
                <SvgColor
                  src={startAdornmentIcon}
                  sx={{ width: 18, height: 18 }}
                />
              </InputAdornment>
            ) : null,
            endAdornment: (
              <InputAdornment position="end">
                {!openDropdown && value && !disabled ? (
                  <Iconify
                    icon="mdi:close"
                    sx={{
                      cursor: "pointer",
                      color: "text.primary",
                      width: 20,
                      height: 20,
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      onChange({ target: { value: "" } });
                    }}
                  />
                ) : (
                  <Iconify
                    icon="eva:arrow-ios-upward-fill"
                    sx={{
                      cursor: disabled ? "not-allowed" : "pointer",
                      color: "text.primary",
                      width: 20,
                      height: 20,
                      transform: `rotateX(${openDropdown ? 0 : -180}deg)`,
                      transition: "transform 0.5s",
                    }}
                    onClick={handleDropdownIconClick}
                  />
                )}
              </InputAdornment>
            ),
          }}
        />

        <Popover
          open={openDropdown}
          anchorEl={containerRef.current}
          onClose={onClose}
          anchorOrigin={{
            vertical: position === "bottom" ? "bottom" : "top",
            horizontal: "left",
          }}
          transformOrigin={{
            vertical: position === "bottom" ? "top" : "bottom",
            horizontal: "left",
          }}
          disableRestoreFocus
          disableEnforceFocus
          disableAutoFocus
          sx={{
            zIndex: 9999,
            "& .MuiPaper-root": {
              width: Math.max(textFieldWidth, 480),
              height: 320,
              borderRadius:
                position === "bottom" ? "0px 0px 4px 4px" : "4px 4px 0px 0px",
              border: "1px solid",
              borderColor: "action.hover",
              p: 0,
              mt: position === "bottom" ? theme.spacing(0.5) : 0,
              mb: position === "top" ? -0.2 : 0,
              boxShadow: "0px 4px 8px rgba(0, 0, 0, 0.1)",
            },
          }}
        >
          <Box sx={{ display: "flex", height: "100%" }}>
            {/* Left sidebar — category filters */}
            <Box
              sx={{
                width: 170,
                borderRight: "1px solid",
                borderColor: "divider",
                p: 1,
                overflow: "auto",
              }}
            >
              <List sx={{ padding: 0 }}>
                {CATEGORIES.map((cat) => {
                  const selected = selectedCategory === cat.value;
                  return (
                    <ListItemButton
                      key={cat.value}
                      selected={selected}
                      onClick={() => setSelectedCategory(cat.value)}
                      sx={{
                        borderRadius: theme.spacing(0.5),
                        gap: 1,
                        py: 0.75,
                        px: 1,
                        mb: 0.25,
                        "&.Mui-selected": {
                          backgroundColor: "action.hover",
                          border: "1px solid",
                          borderColor: "primary.light",
                        },
                      }}
                    >
                      {cat.svgSrc ? (
                        <SvgColor
                          src={cat.svgSrc}
                          sx={{ width: 16, height: 16, flexShrink: 0 }}
                        />
                      ) : (
                        <Iconify
                          icon={cat.iconifyIcon}
                          sx={{ width: 16, height: 16, flexShrink: 0 }}
                        />
                      )}
                      <ListItemText
                        primary={cat.label}
                        primaryTypographyProps={{
                          noWrap: true,
                          typography: "s2_1",
                          sx: {
                            fontWeight: selected ? 600 : 400,
                            fontSize: "13px",
                          },
                        }}
                      />
                    </ListItemButton>
                  );
                })}
              </List>
            </Box>

            <Divider orientation="vertical" flexItem />

            {/* Right panel — filtered items */}
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                p: 0.5,
              }}
            >
              {/* Search */}
              <Box sx={{ p: 1, px: 0.5 }}>
                <TextField
                  size="small"
                  fullWidth
                  placeholder="Search..."
                  value={searchedValue}
                  onChange={(e) => setSearchedValue(e.target.value)}
                  autoFocus
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <Iconify
                          icon="eva:search-fill"
                          sx={{ color: "text.disabled", width: 18, height: 18 }}
                        />
                      </InputAdornment>
                    ),
                  }}
                />
              </Box>

              {/* Scrollable list */}
              <Box
                ref={scrollRef}
                sx={{ flex: 1, overflowY: "auto", px: 0.5, pb: 0.5 }}
              >
                <MenuList sx={{ padding: 0 }}>
                  {filteredOptions.map((option, index) => {
                    const isSelected = option.value === value;
                    return (
                      <MenuItem
                        key={option.value + index}
                        selected={isSelected}
                        onClick={(e) => handleItemClick(e, option)}
                        sx={{
                          borderRadius: theme.spacing(0.5),
                          gap: 1,
                          py: 0.75,
                          minHeight: "unset",
                          "&.Mui-selected": {
                            backgroundColor: "action.hover",
                            border: "1px solid",
                            borderColor: "primary.light",
                          },
                        }}
                      >
                        <SvgColor
                          src={getOptionIcon(option)}
                          sx={{ width: 16, height: 16, flexShrink: 0 }}
                        />
                        <Typography typography="s2_1" noWrap sx={{ flex: 1 }}>
                          {option.label}
                        </Typography>
                        <Typography
                          typography="caption"
                          color="text.secondary"
                          sx={{ flexShrink: 0 }}
                        >
                          {option.sourceType === SourceType.PROMPT
                            ? "Prompt"
                            : option.agentType === AGENT_TYPES.VOICE
                              ? "Voice"
                              : "Chat"}
                        </Typography>
                      </MenuItem>
                    );
                  })}
                  {filteredOptions.length === 0 && options.length === 0 ? (
                    <MenuItem disabled sx={{ opacity: 1 }}>
                      <Box
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "flex-start",
                          gap: 1,
                        }}
                      >
                        <Typography typography="s1" color="text.disabled">
                          No agent definitions yet
                        </Typography>
                        <Chip
                          label="Create agent definition"
                          icon={<Iconify icon="mdi:plus" />}
                          onClick={(e) => {
                            e.stopPropagation();
                            onClose();
                            navigate(
                              "/dashboard/simulate/agent-definitions/create-new-agent-definition",
                            );
                          }}
                          sx={{ cursor: "pointer" }}
                        />
                      </Box>
                    </MenuItem>
                  ) : filteredOptions.length === 0 ? (
                    <MenuItem disabled>
                      <Typography typography="s1" color="text.disabled">
                        No results found
                      </Typography>
                    </MenuItem>
                  ) : null}
                </MenuList>
                {isFetchingNextPage && (
                  <Box sx={{ px: 1 }}>
                    <Skeleton variant="text" height={34} />
                    <Skeleton variant="text" height={34} />
                  </Box>
                )}
              </Box>
            </Box>
          </Box>
        </Popover>
      </>
    );
  },
);

CategorizedSourceDropdownInner.displayName = "CategorizedSourceDropdownInner";

CategorizedSourceDropdownInner.propTypes = {
  value: PropTypes.any,
  onChange: PropTypes.func,
  onBlur: PropTypes.func,
  options: PropTypes.array,
  error: PropTypes.bool,
  helperText: PropTypes.string,
  label: PropTypes.string,
  placeholder: PropTypes.string,
  required: PropTypes.bool,
  disabled: PropTypes.bool,
  onScrollEnd: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
  size: PropTypes.string,
  fullWidth: PropTypes.bool,
  sx: PropTypes.object,
};

// ---------------------------------------------------------------------

const CategorizedSourceDropdown = ({
  fieldName,
  control,
  defaultValue = "",
  onChange: externalOnChange = () => {},
  ...rest
}) => {
  return (
    <Controller
      render={({
        field: { onChange: handleOnChange, value, onBlur: defaultBlur, ref },
        formState: { errors },
      }) => {
        const errorMessage = _.get(errors, `${fieldName}.message`);
        const isError = !!errorMessage;

        return (
          <CategorizedSourceDropdownInner
            onChange={(e) => {
              externalOnChange?.(e);
              handleOnChange(e);
            }}
            ref={ref}
            value={value}
            error={isError}
            helperText={errorMessage}
            onBlur={() => {
              defaultBlur();
              rest?.onBlur?.();
            }}
            {...rest}
          />
        );
      }}
      control={control}
      name={fieldName}
      defaultValue={defaultValue}
    />
  );
};

CategorizedSourceDropdown.propTypes = {
  fieldName: PropTypes.string.isRequired,
  control: PropTypes.any.isRequired,
  defaultValue: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.any.isRequired,
      sourceType: PropTypes.string,
      agentType: PropTypes.string,
    }),
  ),
  onScrollEnd: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
  label: PropTypes.string,
  placeholder: PropTypes.string,
  required: PropTypes.bool,
  disabled: PropTypes.bool,
  fullWidth: PropTypes.bool,
  size: PropTypes.string,
};

export default CategorizedSourceDropdown;
