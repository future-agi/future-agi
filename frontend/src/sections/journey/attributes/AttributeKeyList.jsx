import PropTypes from "prop-types";
import {
  Box,
  List,
  ListItemButton,
  ListItemText,
  TextField,
  InputAdornment,
  Typography,
  Button,
} from "@mui/material";
import Iconify from "src/components/iconify";

const AttributeKeyList = ({
  keys,
  selectedKey,
  onSelectKey,
  hasMore,
  isLoadingMore,
  onLoadMore,
  search,
  onSearchChange,
}) => {
  return (
    <Box
      sx={{
        width: 280,
        borderRight: 1,
        borderColor: "border.default",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ p: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Enter exact attribute key..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
      </Box>
      <List
        sx={{ overflow: "auto", flex: 1, p: 1 }}
        dense
        onScroll={(event) => {
          const list = event.currentTarget;
          if (
            hasMore &&
            !isLoadingMore &&
            list.scrollTop + list.clientHeight >= list.scrollHeight - 32
          ) {
            onLoadMore?.();
          }
        }}
      >
        {keys.map(({ key, type, count, count_exact: countExact }) => (
          <ListItemButton
            key={key}
            selected={selectedKey === key}
            onClick={() => onSelectKey(key)}
            sx={{ borderRadius: "6px", py: 0.5 }}
          >
            <ListItemText
              primary={key}
              secondary={
                countExact && Number.isFinite(count)
                  ? count.toLocaleString() + " spans"
                  : "Recent attribute"
              }
              primaryTypographyProps={{
                fontSize: 13,
                fontWeight: selectedKey === key ? 500 : 400,
                noWrap: true,
              }}
              secondaryTypographyProps={{
                fontSize: 12,
                color: "text.tertiary",
              }}
            />
            <Typography
              component="span"
              sx={{ ml: 1, fontSize: 12, color: "text.tertiary" }}
            >
              {type}
            </Typography>
          </ListItemButton>
        ))}
        {keys.length === 0 && !hasMore && (
          <Box sx={{ p: 2, textAlign: "center", color: "text.secondary" }}>
            No attributes found
          </Box>
        )}
        {isLoadingMore && (
          <Box sx={{ p: 1, textAlign: "center", color: "text.secondary" }}>
            Loading more…
          </Box>
        )}
        {hasMore && !isLoadingMore && (
          <Box sx={{ p: 1, textAlign: "center" }}>
            <Button size="small" onClick={() => onLoadMore?.()}>
              Load more attributes
            </Button>
          </Box>
        )}
      </List>
    </Box>
  );
};

AttributeKeyList.propTypes = {
  keys: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      type: PropTypes.string,
      count: PropTypes.number,
    }),
  ).isRequired,
  selectedKey: PropTypes.string,
  onSelectKey: PropTypes.func.isRequired,
  hasMore: PropTypes.bool,
  isLoadingMore: PropTypes.bool,
  onLoadMore: PropTypes.func,
  search: PropTypes.string.isRequired,
  onSearchChange: PropTypes.func.isRequired,
};

export default AttributeKeyList;
