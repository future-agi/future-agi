package edge

import (
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Generate produces a complete edge worker script (JavaScript) from the edge config.
// The worker handles:
// - Geographic routing to the nearest backend region
// - Weighted load balancing across regions
// - Edge caching using the Cache API, keyed per caller credential
// - Header forwarding including auth
// - SSE streaming passthrough
func Generate(cfg config.EdgeConfig) string {
	if !cfg.Enabled || len(cfg.Regions) == 0 {
		return "// Edge proxy disabled or no regions configured.\n"
	}

	var b strings.Builder

	b.WriteString(header())
	b.WriteString(regionConfig(cfg.Regions))
	b.WriteString(cacheConfig(cfg.Cache))
	b.WriteString(selectBackendFunc())
	b.WriteString(buildCacheKeyFunc())
	b.WriteString(fetchHandler(cfg.Cache.Enabled))
	b.WriteString(footer())

	return b.String()
}

func header() string {
	return `// Agentcc Gateway Edge Proxy — Auto-generated edge worker
// Do not edit manually. Regenerate via: GET /-/admin/edge/config

`
}

func regionConfig(regions []config.EdgeRegionConfig) string {
	var b strings.Builder
	b.WriteString("const BACKENDS = [\n")
	for _, r := range regions {
		b.WriteString(fmt.Sprintf("  { name: %q, url: %q, weight: %d },\n", r.Name, r.Backend, r.Weight))
	}
	b.WriteString("];\n\n")

	b.WriteString("const TOTAL_WEIGHT = BACKENDS.reduce((sum, b) => sum + b.weight, 0);\n\n")
	return b.String()
}

func cacheConfig(cache config.EdgeCacheConfig) string {
	return fmt.Sprintf(`const CACHE_ENABLED = %t;
const CACHE_DEFAULT_TTL = %d; // seconds
const CACHE_MAX_SIZE = %d; // bytes

`, cache.Enabled, cache.DefaultTTL, cache.MaxSize)
}

func selectBackendFunc() string {
	return `// Weighted random backend selection.
function selectBackend() {
  let r = Math.random() * TOTAL_WEIGHT;
  for (const backend of BACKENDS) {
    r -= backend.weight;
    if (r <= 0) return backend;
  }
  return BACKENDS[BACKENDS.length - 1];
}

`
}

func buildCacheKeyFunc() string {
	return `// The credentials the gateway accepts: the Authorization header, the
// x-api-key header, and for the Gemini routes the x-goog-api-key header and
// the ?key= query parameter. "" when the request carries none of them.
function requestCredential(request, url) {
  const parts = [
    request.headers.get("authorization") || "",
    request.headers.get("x-api-key") || "",
    request.headers.get("x-goog-api-key") || "",
    url.searchParams.get("key") || "",
  ];
  return parts.some(p => p !== "") ? parts.join("\n") : "";
}

async function sha256Hex(text) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, "0")).join("");
}

// The edge cache key covers the caller's credential, the path and the body,
// inside the key URL (the Cache API matches on the URL alone): a cached
// response is only served to a request that presents the credential the
// gateway accepted when it produced that response. Without a credential there
// is no key, and the request is never served from or stored in the cache.
async function buildCacheKey(request, body) {
  const url = new URL(request.url);
  const credential = requestCredential(request, url);
  if (credential === "") {
    return null;
  }
  const credentialHash = await sha256Hex(credential);
  const keyHash = await sha256Hex(credentialHash + "\n" + url.pathname + url.search + "\n" + body);
  return new Request(url.origin + "/__cache/" + keyHash, { method: "GET" });
}

// x-agentcc-cache values meaning the gateway's own response cache handled the
// request for this org: the org opted in and the request was cacheable. Any
// other value ("skip", "skip_org_disabled", none) is never stored at the edge.
const GATEWAY_CACHEABLE = new Set(["miss", "hit_exact", "hit_semantic"]);

`
}

func fetchHandler(cacheEnabled bool) string {
	var b strings.Builder

	b.WriteString(`export default {
  async fetch(request, env, ctx) {
    // Only proxy POST requests to API endpoints.
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const backend = selectBackend();
    const url = new URL(request.url);
    const targetURL = backend.url + url.pathname + url.search;

    // Clone the body for potential caching.
    const body = await request.text();

    // Check if this is a streaming request.
    let isStream = false;
    try {
      const parsed = JSON.parse(body);
      isStream = parsed.stream === true;
    } catch (e) {
      // Not JSON, forward as-is.
    }

`)

	if cacheEnabled {
		b.WriteString(`    // Check edge cache (skip for streaming requests and for requests with no
    // credential, which have no cache key).
    const cacheKey = CACHE_ENABLED && !isStream ? await buildCacheKey(request, body) : null;
    if (cacheKey) {
      const cachedResponse = await caches.default.match(cacheKey);
      if (cachedResponse) {
        const resp = new Response(cachedResponse.body, cachedResponse);
        resp.headers.set("x-agentcc-edge-cache", "hit");
        resp.headers.set("x-agentcc-edge-region", backend.name);
        return resp;
      }
    }

`)
	}

	b.WriteString(`    // Forward headers (including auth).
    const headers = new Headers();
    for (const [key, value] of request.headers) {
      headers.set(key, value);
    }
    headers.set("x-agentcc-edge-region", backend.name);

    const proxyResponse = await fetch(targetURL, {
      method: "POST",
      headers: headers,
      body: body,
    });

`)

	if cacheEnabled {
		b.WriteString(`    // Cache non-streaming successful responses that the gateway's response
    // cache handled for the caller's org.
    if (cacheKey && proxyResponse.ok && GATEWAY_CACHEABLE.has(proxyResponse.headers.get("x-agentcc-cache") || "")) {
      const contentLength = parseInt(proxyResponse.headers.get("content-length") || "0", 10);
      if (contentLength <= CACHE_MAX_SIZE || CACHE_MAX_SIZE === 0) {
        const ttlHeader = proxyResponse.headers.get("x-agentcc-cache-ttl");
        const ttl = ttlHeader ? parseInt(ttlHeader, 10) : CACHE_DEFAULT_TTL;
        if (ttl > 0) {
          const responseToCache = proxyResponse.clone();
          const cacheHeaders = new Headers(responseToCache.headers);
          cacheHeaders.set("Cache-Control", "max-age=" + ttl);
          const cacheable = new Response(responseToCache.body, {
            status: responseToCache.status,
            headers: cacheHeaders,
          });
          ctx.waitUntil(caches.default.put(cacheKey, cacheable));
        }
      }
    }

`)
	}

	b.WriteString(`    // Add edge metadata headers.
    const response = new Response(proxyResponse.body, proxyResponse);
    response.headers.set("x-agentcc-edge-region", backend.name);
    response.headers.set("x-agentcc-edge-cache", "miss");
    return response;
  },
};
`)

	return b.String()
}

func footer() string {
	return ""
}
