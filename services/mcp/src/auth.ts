import type { ApiCredentials } from "./types.js";

export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthenticationError";
  }
}

export function credentialsFromHeaders(headers: Headers): ApiCredentials {
  const apiKey = headers.get("x-api-key")?.trim();
  const secretKey = headers.get("x-secret-key")?.trim();
  const authorization = headers.get("authorization")?.trim();

  if (apiKey || secretKey) {
    if (!apiKey || !secretKey) {
      throw new AuthenticationError(
        "X-Api-Key and X-Secret-Key must be provided together",
      );
    }
    if (authorization) {
      throw new AuthenticationError(
        "Use either API key headers or Authorization, not both",
      );
    }
    return { apiKey, secretKey };
  }

  if (authorization) {
    const match = /^Bearer\s+(.+)$/iu.exec(authorization);
    if (!match?.[1]) {
      throw new AuthenticationError("Authorization must use the Bearer scheme");
    }
    return { bearerToken: match[1] };
  }

  throw new AuthenticationError(
    "Provide X-Api-Key with X-Secret-Key, or an Authorization bearer token",
  );
}

export function authorizationHeaders(credentials: ApiCredentials): Headers {
  const headers = new Headers({ Accept: "application/json" });
  if (credentials.apiKey && credentials.secretKey) {
    headers.set("X-Api-Key", credentials.apiKey);
    headers.set("X-Secret-Key", credentials.secretKey);
    return headers;
  }
  if (credentials.bearerToken) {
    headers.set("Authorization", `Bearer ${credentials.bearerToken}`);
    return headers;
  }
  throw new AuthenticationError("Credentials are incomplete");
}
