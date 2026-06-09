package middleware

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// defaultCORSMethods are the methods allowed by default if none are configured.
var defaultCORSMethods = []string{"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}

// defaultCORSHeaders are the headers allowed by default if none are configured.
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
	if len(headers) == 0 {
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
				w.Header().Set("Access-Control-Allow-Headers", headersStr)
				w.Header().Set("Access-Control-Max-Age", maxAgeStr)
				w.WriteHeader(http.StatusNoContent)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
