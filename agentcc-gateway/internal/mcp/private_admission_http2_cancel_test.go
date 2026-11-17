package mcp

import (
	"context"
	"net/http"
	"testing"
	"time"
)

type admissionObservedHTTP2Write struct {
	http.ResponseWriter
	entered  chan struct{}
	returned chan struct{}
}

func (w *admissionObservedHTTP2Write) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *admissionObservedHTTP2Write) Write(body []byte) (int, error) {
	w.entered <- struct{}{}
	n, err := w.ResponseWriter.Write(body)
	w.returned <- struct{}{}
	return n, err
}

func TestAdmissionHTTP2CancelInterruptsFlowControlledWrite(t *testing.T) {
	entered, returned := make(chan struct{}, 512), make(chan struct{}, 512)
	cancelWatch := make(chan context.CancelFunc, 1)
	finished := make(chan error, 1)
	srv := admissionHTTP2Server(t, http.HandlerFunc(func(rw http.ResponseWriter, r *http.Request) {
		a := &admissionTestAuthorizer{decision: admissionAllow()}
		watch := newAdmissionWatch(r.Context(), admissionTestRequest(), admissionAllow().MembershipRevision, func() LiveAuthorizer { return a }, newAdmissionFakeTicker(), nil)
		defer watch.close()
		cancelWatch <- watch.cancel
		raw := &admissionObservedHTTP2Write{ResponseWriter: rw, entered: entered, returned: returned}
		w, err := newAdmissionWriter(raw, r, watch)
		if err != nil {
			finished <- err
			return
		}
		defer w.close()
		body := make([]byte, admissionUnitMaxBytes)
		for i := 0; i < 512; i++ {
			unit, err := w.BeginUnit()
			if err == nil {
				err = w.ReleaseUnit(unit, HostedUnit{Status: 200, Body: body})
			}
			if err != nil {
				finished <- err
				return
			}
		}
		finished <- nil
	}))
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, srv.URL, nil)
	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatal("flow-control fixture did not open")
	}
	defer resp.Body.Close()
	if resp.ProtoMajor != 2 {
		t.Fatal("flow-control fixture is not HTTP/2")
	}
	stop := <-cancelWatch
	defer stop()
	// Leave the body unread. Observe the actual server Write, not an artificial
	// writer lock; the client's HTTP/2 stream window eventually stops progress.
	for i := 0; i < 512; i++ {
		select {
		case <-entered:
		case <-ctx.Done():
			t.Fatal("no flow-controlled write observed")
		}
		select {
		case <-returned:
			continue
		case <-time.After(200 * time.Millisecond):
		}
		stop()
		select {
		case err := <-finished:
			if err == nil {
				t.Fatal("cancelled blocked stream reported success")
			}
		case <-time.After(time.Second):
			t.Fatal("cancellation waited behind a stuck HTTP/2 write")
		}
		return
	}
	t.Fatal("fixture never exhausted the HTTP/2 receive window")
}
