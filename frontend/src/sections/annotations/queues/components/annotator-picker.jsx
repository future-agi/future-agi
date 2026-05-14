import PropTypes from "prop-types";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  Box,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { useOrgMembersInfinite } from "src/api/annotation-queues/annotation-queues";
import { useDebounce } from "src/hooks/use-debounce";
import { QUEUE_ROLES, ROLE_PRIORITY } from "../constants";

const ROLE_OPTIONS = [
  { value: QUEUE_ROLES.ANNOTATOR, label: "Annotator" },
  { value: QUEUE_ROLES.REVIEWER, label: "Reviewer" },
  { value: QUEUE_ROLES.MANAGER, label: "Manager" },
];
const DEFAULT_MEMBER_ROLES = [QUEUE_ROLES.ANNOTATOR];
const CREATOR_DEFAULT_ROLES = [
  QUEUE_ROLES.MANAGER,
  QUEUE_ROLES.REVIEWER,
  QUEUE_ROLES.ANNOTATOR,
];

function normalizeRoles(entry, fallback = DEFAULT_MEMBER_ROLES) {
  const rawRoles = Array.isArray(entry?.roles)
    ? entry.roles
    : entry?.role
      ? [entry.role]
      : fallback;
  const uniqueRoles = rawRoles.filter(
    (role, index) =>
      ROLE_OPTIONS.some((opt) => opt.value === role) &&
      rawRoles.indexOf(role) === index,
  );
  return uniqueRoles.length > 0 ? uniqueRoles : fallback;
}

function primaryRole(roles) {
  return ROLE_PRIORITY.find((role) => roles.includes(role)) || roles[0];
}

AnnotatorPicker.propTypes = {
  value: PropTypes.arrayOf(
    PropTypes.shape({
      userId: PropTypes.string.isRequired,
      role: PropTypes.string,
      roles: PropTypes.arrayOf(PropTypes.string),
    }),
  ),
  onChange: PropTypes.func.isRequired,
  creatorId: PropTypes.string,
  isManager: PropTypes.bool,
};

export default function AnnotatorPicker({
  value = [],
  onChange,
  creatorId,
  isManager = true,
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search.trim(), 300);
  const { user } = useAuthContext();
  const scrollRef = useRef(null);

  const {
    data: members = [],
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useOrgMembersInfinite(user?.organization?.id, debouncedSearch);

  const selectedMap = useMemo(
    () => new Map(value.map((a) => [a.userId, normalizeRoles(a)])),
    [value],
  );

  const handleToggle = (userId) => {
    if (selectedMap.has(userId)) {
      onChange(value.filter((a) => a.userId !== userId));
    } else {
      onChange([
        ...value,
        {
          userId,
          role: QUEUE_ROLES.ANNOTATOR,
          roles: DEFAULT_MEMBER_ROLES,
        },
      ]);
    }
  };

  const handleRoleToggle = (userId, role, { isCreator = false } = {}) => {
    if (isCreator && role === QUEUE_ROLES.MANAGER) return;

    const currentRoles = selectedMap.get(userId) || [];
    const nextRoles = currentRoles.includes(role)
      ? currentRoles.filter((r) => r !== role)
      : [...currentRoles, role];

    if (nextRoles.length === 0) {
      onChange(value.filter((a) => a.userId !== userId));
      return;
    }

    const nextEntry = {
      userId,
      role: primaryRole(nextRoles),
      roles: nextRoles,
    };

    if (selectedMap.has(userId)) {
      onChange(value.map((a) => (a.userId === userId ? nextEntry : a)));
    } else {
      onChange([...value, nextEntry]);
    }
  };

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
      if (hasNextPage && !isFetchingNextPage) {
        fetchNextPage();
      }
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {/* Search */}
      <TextField
        size="small"
        fullWidth
        placeholder="Search members..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Iconify
                icon="eva:search-fill"
                sx={{ color: "text.disabled", width: 16, height: 16 }}
              />
            </InputAdornment>
          ),
        }}
      />

      {/* Checkbox list */}
      <Box
        ref={scrollRef}
        onScroll={handleScroll}
        sx={{
          maxHeight: 220,
          overflow: "auto",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      >
        {members.map((m) => {
          const isSelected = selectedMap.has(m.id);
          const isCreator = creatorId && m.id === creatorId;
          const currentRoles =
            selectedMap.get(m.id) || (isCreator ? CREATOR_DEFAULT_ROLES : []);
          const rowReadOnly = !isManager;
          return (
            <Box
              key={m.id}
              data-testid={`annotator-row-${m.id}`}
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                px: 1,
                py: 0.5,
                borderBottom: "1px solid",
                borderColor: "divider",
                "&:last-child": { borderBottom: 0 },
                bgcolor: isSelected ? "action.selected" : "transparent",
                "&:hover": {
                  bgcolor: isSelected
                    ? "action.selected"
                    : !rowReadOnly
                      ? "action.hover"
                      : "transparent",
                },
              }}
            >
              <Checkbox
                checked={isCreator || isSelected}
                onChange={() =>
                  !rowReadOnly && !isCreator && handleToggle(m.id)
                }
                disabled={rowReadOnly || isCreator}
                size="small"
                sx={{ p: 0.5 }}
              />
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2" noWrap>
                  {m.name || "Unnamed"}
                  {isCreator && (
                    <Typography
                      component="span"
                      variant="caption"
                      color="text.disabled"
                      sx={{ ml: 0.5 }}
                    >
                      (creator)
                    </Typography>
                  )}
                </Typography>
                {m.email && (
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {m.email}
                  </Typography>
                )}
              </Box>

              <Stack
                direction="row"
                spacing={0.5}
                onClick={(e) => e.stopPropagation()}
                sx={{ flexShrink: 0 }}
              >
                {ROLE_OPTIONS.map((opt) => (
                  <FormControlLabel
                    key={opt.value}
                    label={opt.label}
                    control={
                      <Checkbox
                        size="small"
                        checked={currentRoles.includes(opt.value)}
                        disabled={
                          rowReadOnly ||
                          (isCreator && opt.value === QUEUE_ROLES.MANAGER) ||
                          (isSelected &&
                            currentRoles.length === 1 &&
                            currentRoles.includes(opt.value))
                        }
                        onChange={() =>
                          handleRoleToggle(m.id, opt.value, { isCreator })
                        }
                        sx={{ p: 0.25 }}
                      />
                    }
                    sx={{
                      m: 0,
                      "& .MuiFormControlLabel-label": {
                        fontSize: 12,
                        color: rowReadOnly ? "text.disabled" : "text.secondary",
                      },
                    }}
                  />
                ))}
              </Stack>
            </Box>
          );
        })}
        {members.length === 0 && !isFetchingNextPage && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ p: 2, textAlign: "center" }}
          >
            No members found
          </Typography>
        )}
        {isFetchingNextPage && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 1 }}>
            <CircularProgress size={20} />
          </Box>
        )}
      </Box>
    </Box>
  );
}
