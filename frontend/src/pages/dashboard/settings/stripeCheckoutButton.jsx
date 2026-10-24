import React from "react";
import { Button } from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { stripePromise } from "./stripeVariables";
import logger from "src/utils/logger";

const StripeCheckoutButton = () => {
  const handleCheckout = async () => {
    try {
      const stripe = await stripePromise;
      if (!stripe) {
        logger.error("Failed to create checkout session: Stripe not loaded");
        return;
      }

      // Call your backend to create a Stripe checkout session
      const response = await axios.post(
        endpoints.stripe.createCheckoutSession,
        {
          transaction_type: "create_subscription",
        },
      );

      // Use sessionId from the response
      const { error } = await stripe.redirectToCheckout({
        sessionId: response.data.sessionId,
      });

      if (error) {
        logger.error("Error during checkout:", error);
      }
    } catch (err) {
      logger.error("Failed to create checkout session:", err);
    }
  };

  return (
    <div>
      <Button variant="contained" onClick={handleCheckout}>
        Subscribe Now
      </Button>
      {/* <button role="link" onClick={handleCancelSubscription}>
            Cancel Subscription
        </button> */}
      {/* <button role="link" onClick={handleSubscriptionStatus}>
            Subscription Status1
        </button> */}
    </div>
  );
};

export default StripeCheckoutButton;
