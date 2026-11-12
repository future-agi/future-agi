import axios from "src/utils/axios";
import {
  adaptHubBundle,
  validateHubVersion,
} from "../../deployments/lib/hubBundle";
import { checkHubAbort, HUB_LIMITS } from "../../deployments/lib/hubArchive";
import { parsePinnedHubRef } from "../lib/pinnedRef";
import { toHubError } from "./client";
import { hubApi } from "./endpoints";

/** One counted source=deploy download per attempt; no preview requests or fallback version. */
export async function loadHubDeploymentSource(ref, { signal } = {}) {
  const pin = parsePinnedHubRef(ref);
  checkHubAbort(signal);
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  let oversized = false;
  const { publisher, name, version } = pin;
  const options = {
    signal: controller.signal,
    headers: { "Cache-Control": "no-cache" },
  };
  try {
    const first = await axios.get(
      hubApi.version(publisher, name, version),
      options,
    );
    checkHubAbort(controller.signal);
    const metadata = validateHubVersion(pin, first.data);
    const response = await axios.get(
      hubApi.versionBundle(publisher, name, version),
      {
        signal: controller.signal,
        responseType: "arraybuffer",
        params: { source: "deploy" },
        onDownloadProgress: ({ loaded, total }) => {
          if (
            loaded > metadata.bundleBytes ||
            total > metadata.bundleBytes ||
            loaded > HUB_LIMITS.compressedBytes ||
            total > HUB_LIMITS.compressedBytes
          ) {
            oversized = true;
            controller.abort();
          }
        },
      },
    );
    checkHubAbort(controller.signal);
    const header = (key) =>
      response.headers?.get?.(key) ?? response.headers?.[key];
    if (
      header("x-omega-hub-yanked") !== undefined &&
      header("x-omega-hub-yanked") !== null
    )
      throw new Error(
        "This Hub version was yanked. Upload a project manually instead.",
      );
    const archive =
      response.data instanceof Uint8Array
        ? response.data
        : new Uint8Array(response.data);
    // A withdrawal can happen during transfer; never trust an old query-cache entry.
    const fresh = await axios.get(
      hubApi.version(publisher, name, version),
      options,
    );
    checkHubAbort(controller.signal);
    validateHubVersion(pin, fresh.data);
    if (
      ["digest", "bundleSha256", "bundleBytes"].some(
        (key) => metadata[key] !== fresh.data[key],
      )
    )
      throw new Error(
        "The pinned Hub version changed during download. Retry or upload manually.",
      );
    return await adaptHubBundle({
      pin,
      metadata: fresh.data,
      archive,
      bundleSha256: header("x-omega-hub-bundle-sha256") ?? undefined,
      signal: controller.signal,
    });
  } catch (raw) {
    if (oversized)
      throw new Error(
        "The Hub download exceeds its declared size or the archive limit.",
      );
    checkHubAbort(controller.signal);
    throw toHubError(raw);
  } finally {
    signal?.removeEventListener("abort", abort);
  }
}
