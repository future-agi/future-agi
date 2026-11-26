import { Box, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import React, { useRef, useState, useEffect } from "react";
import BasicPricingCard from "./BasicPricingCard";
import GrowthPricingCard from "./GrowthPricingCard";
import EnterPrisePricingCard from "./EnterPrisePricingCard";
import CalculatePricing from "./CalculatePricing";
import PlanBreakdown from "./PlanBreakDown";
import axios, { endpoints } from "src/utils/axios";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import { activePlan } from "./constant";
import { stripePromise } from "../stripeVariables";
import logger from "src/utils/logger";
import { enqueueSnackbar } from "notistack";

const PricingV2 = () => {
  const { user } = useAuthContext();
  const navigate = useNavigate();
  const [meetModalOpen, setMeetModalOpen] = useState(false);
  const planRef = useRef();
  const [containerHeight, setContainerHeight] = useState("2780px");
  const { data } = useQuery({
    queryKey: ["get-user-subscription-details"],
    queryFn: () => axios.get(endpoints.stripe.subscriptionStatus),
    select: (d) => d.data.result,
  });

  useEffect(() => {
    const updateHeight = () => {
      if (planRef.current) {
        // @ts-ignore
        const height = planRef?.current?.scrollHeight;
        setContainerHeight(`${height + 100}px`);
      }
    };

    updateHeight();

    const resizeObserver = new ResizeObserver(updateHeight);
    if (planRef.current) {
      resizeObserver.observe(planRef.current);
    }

    return () => {
      resizeObserver.disconnect();
    };
  }, [data]); // Re-run when data changes

  const redirectGrowth = () => {
    const protocol = window.location.protocol;
    const host = window.location.host;
    const route = "/dashboard/settings/billing";
    const fullUrl = `${protocol}//${host}${route}`;
    if (user?.accessToken) {
      navigate(route);
    } else {
      window.open(fullUrl, "_blank");
    }
  };

  const redirectEnterprise = () => {
    window.open(
      "https://meetings.hubspot.com/salil-kolhe/future-agi-enterprise-plan-query?uuid=c3f1bb4d-2bec-42ab-aac2-652ade745180",
      "_blank",
    );
  };
  const { mutate: createAutoRechargeSession } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.stripe.createAutoRechargeSession, data),
    onSuccess: async (data) => {
      try {
        const stripe = await stripePromise;
        if (!stripe) {
          enqueueSnackbar("Failed to create checkout session", {
            variant: "error",
          });
          return;
        }
        const { error } = await stripe.redirectToCheckout({
          sessionId: data?.data?.sessionId,
        });

        if (error) {
          logger.error("Error during checkout:", error);
        }
      } catch (err) {
        logger.error("Failed to create checkout session:", err);
      }
    },
    onError: (error) => {
      logger.error("Failed to create checkout session:", error);
    },
  });
  const handleAutoRechargeClick = async () => {
    createAutoRechargeSession({ amount: 100 });
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 5.5,
        zIndex: 1,
        position: "absolute",
        left: "77px",
        right: "77px",
      }}
    >
      <PageHeader
        title="Plans & pricing"
        description="Scale your AI evaluations and prompts with feasible pricing that grows with your needs"
        sx={{ mb: 0 }}
      />
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          gap: 2.5,
          alignItems: "center",
        }}
      >
        <BasicPricingCard currentPlan={data?.tier === activePlan.TIER_FREE} />
        <GrowthPricingCard
          redirect={handleAutoRechargeClick}
          currentPlan={
            data?.tier === activePlan.TIER_BASIC ||
            data?.tier === activePlan.TIER_BASIC_YEARLY
          }
        />
        <EnterPrisePricingCard
          redirect={redirectEnterprise}
          meetModalOpen={meetModalOpen}
          setMeetModalOpen={setMeetModalOpen}
          currentPlan={data?.tier === activePlan.TIER_CUSTOM}
        />
      </Box>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2.5,
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            color="text.primary"
            typography="m2"
            fontWeight={"fontWeightSemiBold"}
          >
            Calculate your cost
          </Typography>
          <Typography
            typography="s1"
            color="text.primary"
            fontWeight={"fontWeightRegular"}
          >
            Scale your AI evaluations and prompts with feasible pricing that
            grows with your needs
          </Typography>
        </Box>
        <CalculatePricing
          meetModalOpen={meetModalOpen}
          setMeetModalOpen={setMeetModalOpen}
          redirect={redirectGrowth}
        />
      </Box>
      <div
        style={{
          minWidth: "1000px",
          position: "relative",
          height: containerHeight,
        }}
      >
        <Box
          sx={{
            position: "absolute",
            top: 120,
            left: "-40px",
            right: "-40px",
            zIndex: 1,
            borderRadius: "12px",
            height: "630px",
            backgroundColor: "action.hover",
          }}
        />
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 2,
            height: "calc(100% - 50px)",
            borderRadius: "12px",
            display: "flex",
            gap: 1.5,
            justifyContent: "flex-end",
          }}
        >
          <Box sx={{ width: "calc(25% - 6px)" }} />
          <Box
            sx={{
              width: "calc(25% - 6px)",
              backgroundColor: "background.paper",
              backdropFilter: "blur(7px)",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          />
          <Box
            sx={{
              width: "calc(25% - 6px)",
              backgroundColor: "background.paper",
              backdropFilter: "blur(7px)",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          />
          <Box
            sx={{
              width: "calc(25% - 6px)",
              backgroundColor: "background.paper",
              backdropFilter: "blur(7px)",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
            }}
          />
        </Box>
        <Box
          ref={planRef}
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2.5,
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 2,
          }}
        >
          <PlanBreakdown
            redirectGrowth={redirectGrowth}
            meetModalOpen={meetModalOpen}
            setMeetModalOpen={setMeetModalOpen}
            // redirectEnterprise={redirectEnterprise}
          />
        </Box>
      </div>
    </Box>
  );
};

export default PricingV2;
