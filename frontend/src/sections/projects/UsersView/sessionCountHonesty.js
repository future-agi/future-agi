export const APPROXIMATE_SESSION_COUNT_TOOLTIP =
  "Approximate session count; consolidated session aliases may be counted separately.";

export const formatNumberWithCommas = (value) => {
  if (value == null || isNaN(value)) return value;
  const [intPart, decPart] = value.toString().split(".");
  const formattedInt = Number(intPart).toLocaleString();
  return decPart ? `${formattedInt}.${decPart}` : formattedInt;
};

export const formatUserSessionCount = ({ value, data }) => {
  const formatted = formatNumberWithCommas(value);
  return data?.num_sessions_is_approximate && formatted != null
    ? `~${formatted}`
    : formatted;
};

export const formatUserSessionCountTooltip = ({ data }) =>
  data?.num_sessions_is_approximate ? APPROXIMATE_SESSION_COUNT_TOOLTIP : null;
