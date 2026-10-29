export const generateNameFromEmail = (email) => {
  if (!email) return "";
  return email
    .split("@")[0]
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
};

export const redirectAfterOrgSetup = (destination) => {
  window.location.href = destination;
};
