import { Box, Button, Stack, Typography, useTheme } from "@mui/material";
import React, { useState, useEffect } from "react";
import Iconify from "src/components/iconify/iconify";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm, useWatch } from "react-hook-form";
import { BillingInfoFormValidation } from "./validation";
import { LoadingButton } from "@mui/lab";
import Drawer from "@mui/material/Drawer"; // Import Drawer
import { enqueueSnackbar } from "notistack";
import { Country, State, City } from "country-state-city";
import { debounce } from "lodash";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import logger from "src/utils/logger";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const BillingInfoModal = ({ open, onClose }) => {
  const [isLoading, setIsLoading] = useState(false);
  const [userData, setUserData] = useState(null);
  const [countryOptions, setCountryOptions] = useState([]);
  const [stateOptions, setStateOptions] = useState([]);
  const [cityOptions, setCityOptions] = useState([]);
  const theme = useTheme();

  const { handleSubmit, control, reset, formState, watch, setValue } = useForm({
    resolver: zodResolver(BillingInfoFormValidation),
    defaultValues: {
      name: "",
      email: "",
      company: null,
      billingAddress1: "",
      billingAddress2: null,
      city: "",
      state: "",
      country: "",
      postalCode: "",
      taxId: "",
    },
  });

  const debouncedTrackEvent = debounce((eventName, properties) => {
    trackEvent(eventName, properties);
  }, 500);

  const [
    watchedCountry,
    watchedName,
    watchedEmail,
    watchedBillingAddress1,
    watchedBillingAddress2,
    watchedState,
    watchedPostalCode,
    watchedTaxId,
    watchedCity,
  ] = useWatch({
    control,
    name: [
      "country",
      "name",
      "email",
      "billingAddress1",
      "billingAddress2",
      "state",
      "postalCode",
      "taxId",
      "city",
    ],
  });

  // All previous useEffect hooks for tracking remain the same
  useEffect(() => {
    const name = watchedName;
    if (formState.isDirty && name) {
      debouncedTrackEvent(Events.billingNameEntered, {
        billing_contact_name: name,
      });
    }
  }, [watchedName, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedEmail) {
      debouncedTrackEvent(Events.billingEmailEntered, {
        billing_email_id: watchedEmail,
      });
    }
  }, [watchedEmail, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedCountry) {
      debouncedTrackEvent(Events.companyNameEntered, {
        company: watchedCountry,
      });
    }
  }, [watchedCountry, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (
      formState.isDirty &&
      (watchedBillingAddress1 || watchedBillingAddress2)
    ) {
      debouncedTrackEvent(Events.billingAddressEntered, {
        billing_address: watchedBillingAddress1,
        billing_address2: watchedBillingAddress2,
      });
    }
  }, [
    watchedBillingAddress1,
    watchedBillingAddress2,
    formState.isDirty,
    debouncedTrackEvent,
  ]);

  useEffect(() => {
    if (formState.isDirty && watchedCountry) {
      debouncedTrackEvent(Events.countrySelectionClicked, {
        country: watchedCountry,
      });
    }
  }, [watchedCountry, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedState) {
      debouncedTrackEvent(Events.stateSelectionClicked, {
        state: watchedState,
      });
    }
  }, [watchedState, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedPostalCode) {
      debouncedTrackEvent(Events.postalCodeEntered, {
        postal_code: watchedPostalCode,
      });
    }
  }, [watchedPostalCode, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedTaxId) {
      debouncedTrackEvent(Events.taxIdEntered, { tax_Id: watchedTaxId });
    }
  }, [watchedTaxId, formState.isDirty, debouncedTrackEvent]);

  useEffect(() => {
    if (formState.isDirty && watchedCity) {
      debouncedTrackEvent(Events.citySelectionClicked, { city: watchedCity });
    }
  }, [watchedCity, formState.isDirty, debouncedTrackEvent]);

  const handleGetBillingDetails = async () => {
    try {
      const response = await axios.get(endpoints.stripe.getBillingDetails);
      setUserData(response.data.billing_info);
    } catch (error) {
      logger.error("Error fetching billing details:", error);
    }
  };

  useEffect(() => {
    const countries_data = Country.getAllCountries();
    const countryOptions = countries_data.map((country) => ({
      label: country.name,
      value: country.isoCode,
    }));
    setCountryOptions(countryOptions);

    handleGetBillingDetails();
  }, [open]);

  useEffect(() => {
    if (open && userData) {
      // Reset form with existing user data
      reset({
        name: userData ? userData.name : "",
        email: userData ? userData.email : "",
        company: userData ? userData.company : "",
        billingAddress1: userData ? userData.billingAddress1 : "",
        billingAddress2: userData ? userData.billingAddress2 : "",
        city: userData ? userData.city : "",
        state: userData ? userData.state : "",
        country: userData ? userData.country : "",
        postalCode: userData ? userData.postalCode : "",
        taxId: userData ? userData.taxId : "",
      });

      // Dynamically populate states and cities based on existing data
      if (userData.country) {
        // Load states for the country
        const states_data = State.getStatesOfCountry(userData.country);
        const stateOptions = states_data.map((state) => ({
          label: state.name,
          value: state.isoCode,
        }));
        setStateOptions(stateOptions);

        // If state exists, load cities for that state
        if (userData.state) {
          const cities_data = City.getCitiesOfState(
            userData.country,
            userData.state,
          );
          const cityOptions = cities_data.map((city) => ({
            label: city.name,
            value: city.name,
          }));
          setCityOptions(cityOptions);
        }
      }
    }
  }, [open, userData, reset]);

  const handleEditBillingDetails = async (formValues) => {
    setIsLoading(true);

    const data = {
      name: formValues.name,
      email: formValues.email,
      company: formValues.company,
      billing_address1: formValues.billingAddress1,
      billing_address2: formValues.billingAddress2,
      city: formValues.city,
      state: formValues.state,
      country: formValues.country,
      postal_code: formValues.postalCode,
      tax_id: formValues.taxId,
    };
    trackEvent(Events.updateBillingClicked, {
      [PropertyName.formFields]: {
        billing_contact_name: formValues.name,
        billing_email_id: formValues.email,
        company: formValues.company,
        billing_address: formValues.billingAddress1,
        city: formValues.city,
        country: formValues.country,
        state: formValues.state,
        postal_code: formValues.postalCode,
      },
      email: formValues.email,
      name: formValues.name,
      company_name: formValues.company,
      Country: formValues.country,
      tax_Id: formValues.taxId,
    });

    try {
      await axios.post(endpoints.stripe.updateBillingDetails, data);
    } catch (error) {
      logger.error("Error creating user:", error);
    } finally {
      setIsLoading(false);
      enqueueSnackbar("Billing details updated successfully.", {
        variant: "success",
      });
      onClose();
    }
  };

  const handleCountryChange = (countryCode) => {
    // Populate states for the selected country
    const states_data = State.getStatesOfCountry(countryCode);
    const stateOptions = states_data.map((state) => ({
      label: state.name,
      value: state.isoCode,
    }));
    setStateOptions(stateOptions);

    // Reset state and city
    setValue("state", "");
    setValue("city", "");
    setCityOptions([]); // Clear city options
  };

  const handleStateChange = (stateCode) => {
    const countryCode = watch("country");
    if (countryCode && stateCode) {
      // Populate cities for the selected state
      const cities_data = City.getCitiesOfState(countryCode, stateCode);
      const cityOptions = cities_data.map((city) => ({
        label: city.name,
        value: city.name,
      }));
      setCityOptions(cityOptions);

      // Reset city
      setValue("city", "");
    } else {
      setCityOptions([]);
    }
  };

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      sx={coveredDrawerSx(covered)}
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "480px",
          position: "fixed",
          zIndex: 1000,
          backgroundColor: "background.paper",
          borderTopLeftRadius: 0,
          borderBottomLeftRadius: 0,
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" }, // Make background transparent
        },
      }}
    >
      <form
        onSubmit={handleSubmit(handleEditBillingDetails)}
        style={{ width: "100%" }}
      >
        <Box
          sx={{
            display: open ? "flex" : "none",
            position: "fixed",
            right: 0,
            top: 0,
            height: "100vh",
            width: "100%",
            padding: theme.spacing(2),
            overflow: "hidden",
            backgroundColor: "background.paper",
            // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
            boxShadow: 3,
            zIndex: 1000,
            flexDirection: "column",
          }}
        >
          <Box
            sx={{
              marginBottom: theme.spacing(1),
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              variant="m3"
              color="text.primary"
              fontWeight="fontWeightSemiBold"
            >
              Billing Information
            </Typography>
            <Iconify
              icon="solar:close-linear"
              color="text.primary"
              onClick={onClose}
              sx={{ cursor: "pointer" }}
              width={20}
              height={20}
            />
          </Box>

          <Stack
            direction="column"
            spacing={2.5}
            sx={{
              height: "calc(100vh - 150px)", // Reduced height to remove extra space
              overflowY: "auto",
              "&::-webkit-scrollbar": {
                width: "8px",
              },
              "&::-webkit-scrollbar-track": {
                background: "var(--bg-neutral)",
              },
              "&::-webkit-scrollbar-thumb": {
                background: "var(--text-disabled)",
                borderRadius: "4px",
              },
              "&::-webkit-scrollbar-thumb:hover": {
                background: "var(--text-muted)",
              },
            }}
          >
            <div style={{ marginTop: "0px" }} />

            <FormTextFieldV2
              label="Billing Contact Name"
              placeholder="Enter billing contact name"
              control={control}
              size="small"
              required
              fieldName="name"
              autoFocus
            />
            <FormTextFieldV2
              label="Billing Email ID"
              placeholder="Enter billing email ID"
              control={control}
              size="small"
              required
              fieldName="email"
            />
            <FormTextFieldV2
              label="Company (optional)"
              placeholder="Enter company Name"
              control={control}
              size="small"
              fieldName="company"
            />
            <FormTextFieldV2
              label="Billing Address 1"
              placeholder="Enter billing address 1"
              control={control}
              size="small"
              required
              fieldName="billingAddress1"
            />
            <FormTextFieldV2
              label="Billing Address 2 (optional)"
              placeholder="Enter billing address 2"
              control={control}
              size="small"
              fieldName="billingAddress2"
            />

            <FormSearchSelectFieldControl
              label="Country"
              placeholder="Select Country"
              control={control}
              size="small"
              required
              fieldName="country"
              options={countryOptions}
              onChange={(event) => {
                handleCountryChange(event?.target?.value);
              }}
            />
            <FormSearchSelectFieldControl
              label="State"
              placeholder="Select State"
              control={control}
              isSearchable={true}
              size="small"
              required
              fieldName="state"
              options={stateOptions}
              onChange={(event) => {
                handleStateChange(event?.target?.value);
              }}
            />
            <FormSearchSelectFieldControl
              label="City"
              placeholder="Select City"
              control={control}
              isSearchable={true}
              size="small"
              required
              fieldName="city"
              options={cityOptions}
              sx={{
                "& fieldset": {
                  borderRadius: "8px !important",
                },
              }}
            />
            <FormTextFieldV2
              label="Postal Code"
              placeholder="Enter postal code"
              control={control}
              isSearchable={true}
              size="small"
              required
              fieldName="postalCode"
              inputProps={{
                inputMode: "numeric", // Show numeric keyboard on mobile
                pattern: "[0-9]*", // Allow only numbers
                onKeyPress: (event) => {
                  // Prevent non-numeric input
                  if (!/[0-9]/.test(event.key)) {
                    event.preventDefault();
                  }
                },
              }}
            />
            <FormTextFieldV2
              label="Tax ID"
              placeholder="Enter tax ID"
              control={control}
              size="small"
              required
              fieldName="taxId"
            />
          </Stack>
          <Box
            sx={{
              position: "fixed",
              bottom: 0,
              right: 0,
              width: "550px",
              padding: theme.spacing(2),
              backgroundColor: "background.paper",
              zIndex: 1200, // Higher z-index to ensure it's above other elements
            }}
          >
            <Stack direction="row" spacing={2} sx={{ width: "100%" }}>
              <Button
                variant="outlined"
                onClick={() => {
                  onClose();
                  trackEvent(Events.updateBillingCancelClicked);
                }}
                sx={{ flex: 1, width: "250px" }}
              >
                <Typography
                  variant="s1"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                >
                  Cancel
                </Typography>
              </Button>
              <LoadingButton
                loading={isLoading}
                variant="contained"
                color="primary"
                type="submit"
                sx={{
                  flex: 1,
                  width: "250px",
                  "&:disabled": {
                    color: "common.white",
                    backgroundColor: "action.hover",
                  },
                }}
                disabled={!formState.isValid}
              >
                <Typography
                  variant="s1"
                  fontWeight="fontWeightMedium"
                  color="background.paper"
                >
                  Update Billing Details
                </Typography>
              </LoadingButton>
            </Stack>
          </Box>
        </Box>
      </form>
    </Drawer>
  );
};

BillingInfoModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  userData: PropTypes.object,
  type: PropTypes.string,
};

export default BillingInfoModal;
