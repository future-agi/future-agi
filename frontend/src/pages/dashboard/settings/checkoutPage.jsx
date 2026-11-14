import React, { useState } from "react";
import {
  Box,
  Typography,
  TextField,
  Button,
  Stack,
  MenuItem,
  Grid,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";

export default function CheckoutPage() {
  const [billingDetails, setBillingDetails] = useState("");

  const paymentIcons = [
    { name: "Visa", icon: "logos:visa" },
    { name: "Stripe", icon: "logos:stripe" },
    { name: "PayPal", icon: "logos:paypal" },
    { name: "Mastercard", icon: "logos:mastercard" },
    { name: "GPay", icon: "logos:google-pay" },
  ];

  const countries = [
    "United States",
    "United Kingdom",
    "Canada",
    // Add more countries as needed
  ];

  return (
    <Box sx={{ p: 3, maxWidth: 800, mx: "auto" }}>
      {/* <Typography 
        sx={{ 
          mb: 3, 
          fontSize: '16px', 
          fontWeight: '600',
          color: '#2c3345'
        }}
      >
        Checkout
      </Typography>*/}

      <Typography
        sx={{
          mb: 2,
          fontSize: "16px",
          fontWeight: "600",
          color: "text.primary",
        }}
      >
        Payment Methods
      </Typography>

      {/* <Typography 
        sx={{ 
          mb: 2, 
          fontSize: '16px', 
          fontWeight: '600',
          color: '#2c3345'
        }}
      >
        Card Type
      </Typography> */}

      <Stack direction="row" spacing={2} sx={{ mb: 4 }}>
        {paymentIcons.map((item) => (
          <IconButton key={item.name}>
            <Iconify icon={item.icon} width={40} />
          </IconButton>
        ))}
      </Stack>

      <Grid container spacing={3}>
        <Grid item xs={12}>
          <TextField fullWidth label="Cardholder Name" variant="outlined" />
        </Grid>

        <Grid item xs={12}>
          <TextField fullWidth label="Card Number" variant="outlined" />
        </Grid>

        <Grid item xs={6}>
          <TextField fullWidth label="Expiration Date" variant="outlined" />
        </Grid>

        <Grid item xs={6}>
          <TextField fullWidth label="Expiration Month" variant="outlined" />
        </Grid>

        <Grid item xs={12}>
          <TextField fullWidth label="CVV" variant="outlined" />
        </Grid>

        <Grid item xs={12}>
          <TextField
            select
            fullWidth
            label="Billing Country"
            variant="outlined"
          >
            {countries.map((country) => (
              <MenuItem key={country} value={country}>
                {country}
              </MenuItem>
            ))}
          </TextField>
        </Grid>

        <Grid item xs={12}>
          <Button variant="contained" color="primary" fullWidth sx={{ mb: 4 }}>
            Update Card
          </Button>
        </Grid>

        <Grid item xs={12}>
          <Typography
            sx={{
              mb: 2,
              fontSize: "16px",
              fontWeight: "600",
              color: "text.primary",
            }}
          >
            Billing Details
          </Typography>
          <TextField
            fullWidth
            multiline
            rows={4}
            value={billingDetails}
            onChange={(e) => setBillingDetails(e.target.value)}
            sx={{ mb: 3 }}
          />
        </Grid>

        <Grid item xs={12}>
          <Button variant="contained" color="primary" fullWidth size="large">
            Save and Review Order
          </Button>
        </Grid>
      </Grid>
    </Box>
  );
}
