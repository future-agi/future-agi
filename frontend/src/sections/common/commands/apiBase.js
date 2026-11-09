// the capability contract(4): the ONE API base every printed command, snippet and
// generated file uses. It is this build's HOST_API (config-global): the
// prototype prints its mock/configured host, any other build its
// VITE_HOST_API. No fixed production host is ever printed for a user to
// copy. Pure; no network.
import { HOST_API } from "src/config-global";

/** `host` trimmed, without trailing slashes ("" when unset). */
export const apiBaseOf = (host) =>
  String(host ?? "")
    .trim()
    .replace(/\/+$/, "");

/** The Future AGI API base printed in commands and generated files. */
export const FI_BASE_URL = apiBaseOf(HOST_API);
