package middleware

import (
	"log/slog"
	"net/http"
	"runtime"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// writerSentinel tracks whether WriteHeader has been called.
type writerSentinel struct {
	http.ResponseWriter
	headersSent bool
}

func (w *writerSentinel) WriteHeader(code int) {
	w.headersSent = true
	w.ResponseWriter.WriteHeader(code)
}

func (w *writerSentinel) Write(b []byte) (int, error) {
	w.headersSent = true
	return w.ResponseWriter.Write(b)
}

// Unwrap allows http.ResponseController to find wrapped interfaces.
func (w *writerSentinel) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

// Flush delegates to the underlying ResponseWriter if it supports flushing.
func (w *writerSentinel) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Recovery wraps a handler with panic recovery.
func Recovery(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws := &writerSentinel{ResponseWriter: w}
		defer func() {
			if rec := recover(); rec != nil {
				buf := make([]byte, 4096)
				n := runtime.Stack(buf, false)
				slog.Error("panic recovered",
					"panic", rec,
					"stack", string(buf[:n]),
					"method", r.Method,
					"path", r.URL.Path,
					"request_id", models.GetRequestID(r.Context()),
				)
				// Only write an error response if headers haven't been sent yet.
				if !ws.headersSent {
					models.WriteError(w, models.ErrInternal("internal server error"))
				}
			}
		}()
		next.ServeHTTP(ws, r)
	})
}
