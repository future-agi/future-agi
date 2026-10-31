import React from "react";
import PropTypes from "prop-types";
import { useState, useCallback } from "react";

import Card from "@mui/material/Card";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import IconButton from "@mui/material/IconButton";
import CardHeader from "@mui/material/CardHeader";

import { useBoolean } from "src/hooks/use-boolean";

import Iconify from "src/components/iconify";
import CustomPopover, { usePopover } from "src/components/custom-popover";

import { AddressItem, AddressNewForm } from "../address";
import logger from "src/utils/logger";

// ----------------------------------------------------------------------

export default function AccountBillingAddress({ addressBook }) {
  const [addressId, setAddressId] = useState("");

  const popover = usePopover();

  const addressForm = useBoolean();

  const handleAddNewAddress = useCallback((address) => {
    logger.debug("ADDRESS", address);
  }, []);

  const handleSelectedId = useCallback(
    (event, id) => {
      popover.onOpen(event);
      setAddressId(id);
    },
    [popover],
  );

  const handleClose = useCallback(() => {
    popover.onClose();
    setAddressId("");
  }, [popover]);

  return (
    <>
      <Card>
        <CardHeader
          title="Address Book"
          action={
            <Button
              size="small"
              color="primary"
              startIcon={<Iconify icon="solar:add-linear" />}
              onClick={addressForm.onTrue}
            >
              Address
            </Button>
          }
        />

        <Stack spacing={2.5} sx={{ p: 3 }}>
          {addressBook.map((address) => (
            <AddressItem
              variant="outlined"
              key={address.id}
              address={address}
              action={
                <IconButton
                  onClick={(event) => {
                    handleSelectedId(event, `${address.id}`);
                  }}
                  sx={{ position: "absolute", top: 8, right: 8 }}
                >
                  <Iconify icon="solar:menu-dots-vertical-linear" />
                </IconButton>
              }
              sx={{
                p: 2.5,
                borderRadius: 1,
              }}
            />
          ))}
        </Stack>
      </Card>

      <CustomPopover open={popover.open} onClose={handleClose}>
        <MenuItem
          onClick={() => {
            handleClose();
            logger.debug("SET AS PRIMARY", addressId);
          }}
        >
          <Iconify icon="solar:star-bold" />
          Set as primary
        </MenuItem>

        <MenuItem
          onClick={() => {
            handleClose();
            logger.debug("EDIT", addressId);
          }}
        >
          <Iconify icon="solar:pen-bold" />
          Edit
        </MenuItem>

        <MenuItem
          onClick={() => {
            handleClose();
            logger.debug("DELETE", addressId);
          }}
          // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
          sx={{ color: "status.danger.fg" }}
        >
          <Iconify icon="solar:trash-bin-trash-bold" />
          Delete
        </MenuItem>
      </CustomPopover>

      <AddressNewForm
        open={addressForm.value}
        onClose={addressForm.onFalse}
        onCreate={handleAddNewAddress}
      />
    </>
  );
}

AccountBillingAddress.propTypes = {
  addressBook: PropTypes.array,
};
