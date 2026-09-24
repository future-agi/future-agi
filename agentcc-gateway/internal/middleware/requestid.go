package middleware

import (
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/oklog/ulid/v2"
)

func generateULID() string {
	return ulid.MustNew(ulid.Timestamp(time.Now()), ulid.DefaultEntropy()).String()
}

// RequestID generates a ULID for each request and sets it in the context and response header.
func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := generateULID()
		w.Header().Set("x-agentcc-request-id", id)

		// Use caller-provided trace ID or generate one.
		traceID := r.Header.Get("x-agentcc-trace-id")
		if traceID == "" {
			traceID = generateULID()
		}
		w.Header().Set("x-agentcc-trace-id", traceID)

		ctx := models.WithRequestID(r.Context(), id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
