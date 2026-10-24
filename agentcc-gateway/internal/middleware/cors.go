package middleware

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// defaultCORSMethods are the methods allowed by default if none are configured.
var defaultCORSMethods = []string{"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}

// defaultCORSHeaders are the headers allowed by default if none are
// configured: every documented per-request header. Of the agent context
// headers it holds three, x-agentcc-trace-id, x-agentcc-user-id and
// x-agentcc-session-id; the others need cors.allowed_headers. A
// preflight that asks for x-agentcc-property-<name> headers is answered
// with those names too (requestedPropertyHeaders).
var defaultCORSHeaders = []string{
	"Authorization",
	"Content-Type",
	"X-Agentcc-Trace-Id",
	"X-Agentcc-Timeout",
	"X-Agentcc-Request-Timeout",
	"X-Agentcc-User-Id",
	"X-Agentcc-Session-Id",
	"X-Agentcc-Config",
	"X-Guardrail-Policy",
	"Cache-Control",
	"X-Agentcc-Session-Name",
	"X-Agentcc-Session-Path",
	"X-Agentcc-Request-Id",
	"X-Agentcc-Metadata",
	"X-Agentcc-Cache-Ttl",
	"X-Agentcc-Cache-Namespace",
	"X-Agentcc-Cache-Force-Refresh",
	"X-Agentcc-Provider-Lock",
	"X-Agentcc-Complexity-Override",
	"X-Agentcc-Guardrail-Policy",
	"X-Agentcc-Max-Attempts",
	"X-Agentcc-Retry-Delay-Ms",
	"X-Agentcc-Backoff",
	"X-Agentcc-Auto-Preference",
	"X-Agentcc-Log-Content",
}

// defaultExposedHeaders are the response headers browsers are allowed to read.
var defaultExposedHeaders = []string{
	"X-Agentcc-Request-Id",
	"X-Agentcc-Trace-Id",
	"X-Agentcc-Provider",
	"X-Agentcc-Model-Used",
	"X-Agentcc-Latency-Ms",
	"X-Agentcc-Cost",
	"X-Agentcc-Cache",
	"X-Agentcc-Timeout-Ms",
	"X-RateLimit-Limit",
	"X-RateLimit-Remaining",
	"X-RateLimit-Reset",
	"X-Agentcc-Client-Request-Id",
	"X-Agentcc-Config-Ignored",
	"X-Agentcc-Guardrail-Triggered",
	"X-Agentcc-Guardrail-Action",
	"X-Agentcc-Guardrails-Triggered",
	"X-Agentcc-Fallback-Used",
	"X-Agentcc-Routing-Strategy",
	"X-Agentcc-Credits-Remaining",
	"X-Ratelimit-Limit-Requests",
	"X-Ratelimit-Remaining-Requests",
	"X-Ratelimit-Reset-Requests",
}

// CORS returns middleware that handles CORS preflight and response headers.
func CORS(cfg config.CORSConfig) func(http.Handler) http.Handler {
	origins := cfg.AllowedOrigins
	if len(origins) == 0 {
		origins = []string{"*"}
	}

	methods := cfg.AllowedMethods
	if len(methods) == 0 {
		methods = defaultCORSMethods
	}

	headers := cfg.AllowedHeaders
	defaultHeaders := len(headers) == 0
	if defaultHeaders {
		headers = defaultCORSHeaders
	}

	exposed := cfg.ExposedHeaders
	if len(exposed) == 0 {
		exposed = defaultExposedHeaders
	}

	maxAge := cfg.MaxAge
	if maxAge == 0 {
		maxAge = 86400 // 24 hours
	}

	methodsStr := strings.Join(methods, ", ")
	headersStr := strings.Join(headers, ", ")
	exposedStr := strings.Join(exposed, ", ")
	maxAgeStr := strconv.Itoa(maxAge)

	allowAll := len(origins) == 1 && origins[0] == "*"
	originSet := make(map[string]bool, len(origins))
	for _, o := range origins {
		originSet[o] = true
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if origin == "" {
				// Not a CORS request — pass through.
				next.ServeHTTP(w, r)
				return
			}

			// Check if origin is allowed.
			allowed := allowAll || originSet[origin]
			if !allowed {
				// Origin not in allow list — pass through without CORS headers.
				next.ServeHTTP(w, r)
				return
			}

			// Set the allowed origin (use the specific origin, not "*", when credentials are enabled).
			if allowAll && !cfg.AllowCredentials {
				w.Header().Set("Access-Control-Allow-Origin", "*")
			} else {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Vary", "Origin")
			}

			if cfg.AllowCredentials {
				w.Header().Set("Access-Control-Allow-Credentials", "true")
			}

			w.Header().Set("Access-Control-Expose-Headers", exposedStr)

			// Handle preflight.
			if r.Method == http.MethodOptions {
				w.Header().Set("Access-Control-Allow-Methods", methodsStr)
				allowHeaders := headersStr
				if defaultHeaders {
					if props := requestedPropertyHeaders(r.Header.Get("Access-Control-Request-Headers")); props != "" {
						allowHeaders += ", " + props
					}
				}
				w.Header().Set("Access-Control-Allow-Headers", allowHeaders)
				w.Header().Set("Access-Control-Max-Age", maxAgeStr)
				w.WriteHeader(http.StatusNoContent)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}

// requestedPropertyHeaders is the x-agentcc-property-<name> headers a
// preflight's Access-Control-Request-Headers asks for, comma-separated: a
// property's name is the caller's own, so the default list cannot name it.
func requestedPropertyHeaders(requested string) string {
	var out []string
	for _, name := range strings.Split(requested, ",") {
		name = strings.TrimSpace(name)
		if len(name) > len(hdrPropertyPrefix) && len(name) <= len(hdrPropertyPrefix)+maxPropertyNameLen &&
			isPropertyHeader(name) && validPropertyName(strings.ToLower(name[len(hdrPropertyPrefix):])) {
			out = append(out, name)
		}
	}
	return strings.Join(out, ", ")
}
