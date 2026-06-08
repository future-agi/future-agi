import React from "react";
import { Helmet } from "react-helmet-async";
import OnboardingHomeView from "src/sections/onboarding-home/OnboardingHomeView";

export default function Home() {
  return (
    <>
      <Helmet>
        <title>Home | FutureAGI</title>
      </Helmet>
      <OnboardingHomeView />
    </>
  );
}
