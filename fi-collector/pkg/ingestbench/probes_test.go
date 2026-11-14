package ingestbench

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestReadWindowDeadlineCensorsRequestButNotClientTimeout(t *testing.T) {
	for _, mode := range []string{"window-before-headers", "window-during-body", "client-timeout"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls.Add(1) == 1 {
					fmt.Fprint(w, `{"data":[]}`)
					return
				}
				if mode == "window-during-body" {
					fmt.Fprint(w, `{"data":`)
					w.(http.Flusher).Flush()
				}
				timer := time.NewTimer(250 * time.Millisecond)
				defer timer.Stop()
				select {
				case <-r.Context().Done():
					return
				case <-timer.C:
				}
				if mode == "window-during-body" {
					fmt.Fprint(w, `[]}`)
				} else {
					fmt.Fprint(w, `{"data":[]}`)
				}
			}))
			defer srv.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 1100*time.Millisecond)
			defer cancel()
			client := testConfig().client()
			defer client.CloseIdleConnections()
			if mode == "client-timeout" {
				client.Timeout = 20 * time.Millisecond
			}
			p := ReadProbe{Name: "trace-list", URL: srv.URL, Method: "GET", ExpectStatus: 200, ExpectKey: "data", IntervalMS: 1000}
			r := runReadProbe(ctx, client, p, nil)
			if r.Successes != 1 {
				t.Fatalf("initial success lost: %+v", r)
			}
			if mode == "client-timeout" {
				if r.Errors != 1 || r.Censored != 0 || r.Status != "errors" || r.FailureLatency.Count != 1 {
					t.Fatalf("client timeout hidden: %+v", r)
				}
			} else if r.Errors != 0 || r.Censored != 1 || r.Status != "measured-with-censoring" || r.FailureLatency.Count != 0 {
				t.Fatalf("window cancellation misreported as server failure: %+v", r)
			} else if r.CensoredElapsed.Count != 1 || r.CensoredElapsed.Max <= 0 || r.CensoredElapsed.P50 > r.CensoredElapsed.Max {
				// The request started at about 1000 ms and was cut off at 1100 ms:
				// its elapsed time is kept as a lower bound on its latency.
				t.Fatalf("censored request elapsed not recorded: %+v", r.CensoredElapsed)
			}
			if mode == "client-timeout" && r.CensoredElapsed.Count != 0 {
				t.Fatalf("a client timeout recorded as censored: %+v", r.CensoredElapsed)
			}
		})
	}
}
