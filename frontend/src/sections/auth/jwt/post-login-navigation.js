import { isSafePostLoginReturnTo } from "src/sections/onboarding-home/utils/post-login-routing";

const localStorageOrNull = () => {
  if (typeof window === "undefined") return null;
  return window.localStorage;
};

export const isSafeAuthReturnTo = (returnTo) =>
  isSafePostLoginReturnTo(returnTo);

export const resolveAuthSuccessRoute = ({ returnTo, fallbackPath }) =>
  isSafeAuthReturnTo(returnTo) ? returnTo : fallbackPath;

export const prepareAuthSuccessPostLoginResolution = ({
  returnTo,
  preserveStoredReturnTo = false,
  storage = localStorageOrNull(),
} = {}) => {
  if (!storage) return;
  storage.removeItem("initial-render");
  if (!preserveStoredReturnTo && !isSafeAuthReturnTo(returnTo)) {
    storage.removeItem("redirectUrl");
  }
};

export const navigateAfterAuthSuccess = ({
  router,
  returnTo,
  fallbackPath,
  preserveStoredReturnTo = false,
  storage,
}) => {
  prepareAuthSuccessPostLoginResolution({
    returnTo,
    preserveStoredReturnTo,
    storage,
  });
  const targetRoute = resolveAuthSuccessRoute({ returnTo, fallbackPath });
  router.push(targetRoute);
  return targetRoute;
};
