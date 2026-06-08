const STORAGE_PREFIX = "futureagi:onboarding-destination-tour-dismissed:v1";
const SESSION_USER_ID_KEY = "currentUserId";
const ANONYMOUS_ID = "anonymous";

const safeStorage = (storageName) => {
  if (typeof window === "undefined") return null;
  try {
    return window[storageName] || null;
  } catch {
    return null;
  }
};

export const destinationTourStorageIdentity = () => {
  const sessionStorage = safeStorage("sessionStorage");
  const userId = sessionStorage?.getItem(SESSION_USER_ID_KEY);
  return userId || ANONYMOUS_ID;
};

export const destinationTourDismissalKey = (
  identity = destinationTourStorageIdentity(),
) => `${STORAGE_PREFIX}:${identity || ANONYMOUS_ID}`;

export const readDestinationTourDismissals = ({
  identity = destinationTourStorageIdentity(),
} = {}) => {
  const localStorage = safeStorage("localStorage");
  if (!localStorage) return new Set();

  try {
    const raw = localStorage.getItem(destinationTourDismissalKey(identity));
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter(Boolean) : []);
  } catch {
    return new Set();
  }
};

const writeDestinationTourDismissals = ({ dismissals, identity }) => {
  const localStorage = safeStorage("localStorage");
  if (!localStorage) return;

  const values = Array.from(dismissals || [])
    .filter(Boolean)
    .sort();
  localStorage.setItem(
    destinationTourDismissalKey(identity),
    JSON.stringify(values),
  );
};

export const isDestinationTourAnchorDismissed = ({
  anchor,
  identity = destinationTourStorageIdentity(),
} = {}) => {
  if (!anchor) return false;
  return readDestinationTourDismissals({ identity }).has(anchor);
};

export const dismissDestinationTourAnchor = ({
  anchor,
  identity = destinationTourStorageIdentity(),
} = {}) => {
  if (!anchor) return readDestinationTourDismissals({ identity });

  const dismissals = readDestinationTourDismissals({ identity });
  dismissals.add(anchor);
  writeDestinationTourDismissals({ dismissals, identity });
  return dismissals;
};

export const resetDestinationTourAnchorDismissal = ({
  anchor,
  identity = destinationTourStorageIdentity(),
} = {}) => {
  const dismissals = readDestinationTourDismissals({ identity });
  if (anchor) {
    dismissals.delete(anchor);
    writeDestinationTourDismissals({ dismissals, identity });
  }
  return dismissals;
};

export const isDestinationTourReplay = (searchParams) => {
  const value = searchParams?.get?.("tour_replay");
  return value === "1" || value === "true";
};
