import { createContext, useContext } from "react";

export const PersonalKeyContext = createContext(null);

export function usePersonalKeyText() {
  const context = useContext(PersonalKeyContext);
  return (text) => {
    if (typeof text !== "string" || !context?.pair) return text;
    return text
      .replace(
        /<YOUR_(?:FI_)?API_KEY>|YOUR_(?:FI_)?API_KEY|<your_api_key>/g,
        () => context.pair.apiKey,
      )
      .replace(
        /<YOUR_(?:FI_)?SECRET_KEY>|YOUR_(?:FI_)?SECRET_KEY|<your_secret_key>/g,
        () => context.pair.secretKey,
      );
  };
}
