import React from "react";
import PropTypes from "prop-types";
import { useState, useCallback } from "react";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Dialog from "@mui/material/Dialog";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import InputAdornment from "@mui/material/InputAdornment";
import ListItemButton, {
  listItemButtonClasses,
} from "@mui/material/ListItemButton";

import Label from "src/components/label";
import Iconify from "src/components/iconify";
import SearchNotFound from "src/components/search-not-found";

// ----------------------------------------------------------------------

export default function AddressListDialog({
  title = "Address Book",
  list,
  action,
  //
  open,
  onClose,
  //
  selected,
  onSelect,
}) {
  const [searchAddress, setSearchAddress] = useState("");

  const dataFiltered = applyFilter({
    inputData: list,
    query: searchAddress,
  });

  const notFound = !dataFiltered.length && !!searchAddress;

  const handleSearchAddress = useCallback((event) => {
    setSearchAddress(event.target.value);
  }, []);

  const handleSelectAddress = useCallback(
    (address) => {
      onSelect(address);
      setSearchAddress("");
      onClose();
    },
    [onClose, onSelect],
  );

  const renderList = (
    <Stack
      spacing={0.5}
      sx={{
        p: 0.5,
        maxHeight: 80 * 8,
        overflowX: "hidden",
      }}
    >
      {dataFiltered.map((address) => (
        <Stack
          key={address.id}
          spacing={0.5}
          component={ListItemButton}
          selected={selected(`${address.id}`)}
          onClick={() => handleSelectAddress(address)}
          sx={{
            py: 1,
            px: 1.5,
            borderRadius: 1,
            flexDirection: "column",
            alignItems: "flex-start",
            [`&.${listItemButtonClasses.selected}`]: {
              bgcolor: "action.selected",
              "&:hover": {
                bgcolor: "action.selected",
              },
            },
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography variant="subtitle2">{address.name}</Typography>

            {address.primary && <Label color="info">Default</Label>}
          </Stack>

          {address.company && (
            <Box sx={{ color: "primary.main", typography: "caption" }}>
              {address.company}
            </Box>
          )}

          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {address.fullAddress}
          </Typography>

          {address.phoneNumber && (
            <Typography variant="body2" sx={{ color: "text.secondary" }}>
              {address.phoneNumber}
            </Typography>
          )}
        </Stack>
      ))}
    </Stack>
  );

  return (
    <Dialog
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 560 } }}
      open={open}
      onClose={onClose}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ p: 3, pr: 1.5 }}
      >
        <Typography variant="h6"> {title} </Typography>

        {action && action}
      </Stack>

      <Stack sx={{ p: 2, pt: 0 }}>
        <TextField
          value={searchAddress}
          onChange={handleSearchAddress}
          placeholder="Search..."
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
      </Stack>

      {notFound ? (
        <SearchNotFound query={searchAddress} sx={{ px: 3, pt: 5, pb: 10 }} />
      ) : (
        renderList
      )}
    </Dialog>
  );
}

AddressListDialog.propTypes = {
  action: PropTypes.node,
  list: PropTypes.array,
  onClose: PropTypes.func,
  onSelect: PropTypes.func,
  open: PropTypes.bool,
  selected: PropTypes.func,
  title: PropTypes.string,
};

// ----------------------------------------------------------------------

function applyFilter({ inputData, query }) {
  if (query) {
    return inputData.filter(
      (address) =>
        address.name.toLowerCase().indexOf(query.toLowerCase()) !== -1 ||
        address.fullAddress.toLowerCase().indexOf(query.toLowerCase()) !== -1 ||
        `${address.company}`.toLowerCase().indexOf(query.toLowerCase()) !== -1,
    );
  }

  return inputData;
}
