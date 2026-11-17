package byteplus

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

func TestReconcile_UniqueZeroAmbiguous(t *testing.T) {
	for _, n := range []int{0, 1, 2} {
		t.Run(fmt.Sprint(n), func(t *testing.T) {
			var token string
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Query().Get("filter.model") != model || r.URL.Query().Get("page_num") != "1" {
					t.Error(r.URL.String())
				}
				fmt.Fprintf(w, `{"total":%d,"items":[`, n+2)
				for i := 0; i < n; i++ {
					if i > 0 {
						fmt.Fprint(w, ",")
					}
					fmt.Fprintf(w, `{"id":"cgt-%d","model":%q,"status":"queued","safety_identifier":%q,"created_at":1700000000}`, i, model, token)
				}
				if n > 0 {
					fmt.Fprint(w, ",")
				}
				fmt.Fprintf(w, `{"id":"wrong-user","model":%q,"safety_identifier":"other","created_at":1700000000},{"id":"old","model":%q,"safety_identifier":%q,"created_at":1600000000}]}`, model, model, token)
			})
			j := job()
			corr := prepare(t, a, j)
			token = corr.Token
			got, e := a.Reconcile(context.Background(), j, corr)
			if e != nil || got.Found != (n == 1) || got.ProvenAbsent {
				t.Fatal(got, e)
			}
			if n == 1 && got.ProviderJobID != "cgt-0" {
				t.Fatal(got)
			}
		})
	}
}
func TestReconcile_PagesAndLimiter(t *testing.T) {
	var calls atomic.Int32
	var token string
	a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		if r.URL.Query().Get("page_num") != fmt.Sprint(n) {
			t.Error(r.URL.RawQuery)
		}
		if n == 1 {
			fmt.Fprintf(w, `{"total":2,"items":[{"id":"cgt-1","model":%q,"status":"queued","safety_identifier":%q,"created_at":1700000000}]}`, model, token)
		} else {
			fmt.Fprintf(w, `{"total":2,"items":[{"id":"cgt-2","model":%q,"status":"queued","safety_identifier":%q,"created_at":1700000000}]}`, model, token)
		}
	})
	a.pageSize = 1
	j := job()
	corr := prepare(t, a, j)
	token = corr.Token
	started := time.Now()
	got, e := a.Reconcile(context.Background(), j, corr)
	if e != nil || got.Found || got.ProvenAbsent || calls.Load() != 2 || time.Since(started) < 950*time.Millisecond {
		t.Fatal(got, e, calls.Load(), time.Since(started))
	}
}
func TestReconcile_IncompleteListNeverFound(t *testing.T) {
	for _, body := range []string{`{"total":1}`, `{}`, `{"total":2,"items":[]}`, `{"total":0,"items":null}`} {
		a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) })
		j := job()
		got, e := a.Reconcile(context.Background(), j, prepare(t, a, j))
		if got.Found || got.ProvenAbsent || e == nil {
			t.Fatal(got, e)
		}
	}
}
func TestCancel_RunningCode(t *testing.T) {
	a := testAdapter(t, func(http.ResponseWriter, *http.Request) { t.Fatal("running cancel contacted provider") })
	got, e := a.Cancel(context.Background(), video.ProviderRef{ProviderJobID: "cgt-test", ProviderState: "running"})
	var unavailable *video.UpstreamError
	if !errors.As(e, &unavailable) || unavailable.Code != "cancel_not_available" || got.State != video.CancelNotAvailable {
		t.Fatal(got, e)
	}
}

func TestReconcile_WindowShapeAndTerminalStates(t *testing.T) {
	for _, tc := range []struct {
		name, status string
		created      int64
		shape        string
		found        bool
	}{{"lower boundary", "queued", 1699999940, "", true}, {"upper boundary", "running", 1700000090, "", true}, {"before", "queued", 1699999939, "", false}, {"after", "queued", 1700000091, "", false}, {"wrong shape", "queued", 1700000000, `,"resolution":"480p"`, false}, {"wrong duration", "queued", 1700000000, `,"duration":12`, false}, {"expired", "expired", 1700000000, "", true}, {"cancelled", "cancelled", 1700000000, "", true}, {"unknown", "new_state", 1700000000, "", false}} {
		t.Run(tc.name, func(t *testing.T) {
			var token string
			a := testAdapter(t, func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprintf(w, `{"total":1,"items":[{"id":"cgt-1","model":%q,"status":%q,"safety_identifier":%q,"created_at":%d%s}]}`, model, tc.status, token, tc.created, tc.shape)
			})
			j := job()
			c := prepare(t, a, j)
			token = c.Token
			got, err := a.Reconcile(context.Background(), j, c)
			if got.Found != tc.found || got.ProvenAbsent || tc.status != "new_state" && err != nil || tc.status == "new_state" && err == nil {
				t.Fatal(got, err)
			}
		})
	}
}
