package middleware

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

type recoveryFlushFailure struct{ *httptest.ResponseRecorder }

func (recoveryFlushFailure) FlushError() error { return io.ErrClosedPipe }

type recoveryUnwrapOnly struct{ http.ResponseWriter }

func (w recoveryUnwrapOnly) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func TestRecoveryResponseControllerPreservesFlushError(t *testing.T) {
	for _, unwrap := range []bool{false, true} {
		recorder := httptest.NewRecorder()
		var raw http.ResponseWriter = recoveryFlushFailure{recorder}
		if unwrap {
			raw = recoveryUnwrapOnly{raw}
		}
		Recovery(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte("accepted"))
			if err := http.NewResponseController(w).Flush(); !errors.Is(err, io.ErrClosedPipe) {
				t.Error("Recovery swallowed FlushError")
			}
			if r.Context().Err() != nil {
				t.Error("flush-only failure must not depend on request cancellation")
			}
		})).ServeHTTP(raw, httptest.NewRequest(http.MethodGet, "/", nil))
		if recorder.Code != http.StatusAccepted || recorder.Body.String() != "accepted" {
			t.Fatal("error-preserving flush changed status or body")
		}
	}
}

func TestRecoveryFlushPreservesNormalHTTPBehavior(t *testing.T) {
	for _, controller := range []bool{false, true} {
		recorder := httptest.NewRecorder()
		Recovery(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("X-Test", "retained")
			if controller {
				if err := http.NewResponseController(w).Flush(); err != nil {
					t.Error("normal flush failed")
				}
			} else {
				w.(http.Flusher).Flush()
			}
			// Flush commits an implicit 200. Recovery must not append an error
			// response if the handler subsequently panics.
			panic("synthetic post-flush panic")
		})).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/", nil))
		if !recorder.Flushed || recorder.Code != http.StatusOK || recorder.Body.Len() != 0 || recorder.Header().Get("X-Test") != "retained" {
			t.Fatal("Recovery changed an already-flushed HTTP response")
		}
	}
}

func TestRecoveryUnsupportedFlushRemainsUnsupported(t *testing.T) {
	raw := struct{ http.ResponseWriter }{httptest.NewRecorder()}
	Recovery(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if !errors.Is(http.NewResponseController(w).Flush(), http.ErrNotSupported) {
			t.Error("unsupported flush was reported as successful")
		}
	})).ServeHTTP(raw, httptest.NewRequest(http.MethodGet, "/", nil))
}
