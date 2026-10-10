// Package videotest supplies local-only scripted HTTP fixtures for adapter and
// lifecycle tests. Its adapter speaks this test protocol, not any real API.
package videotest

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"time"
)

type Reply struct {
	Status int
	Header http.Header
	Body   []byte
	Delay  time.Duration
}

func JSONReply(v any) Reply {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return Reply{Body: b, Header: http.Header{"Content-Type": []string{"application/json"}}}
}

type Script struct {
	Submit              Reply
	Poll                []Reply
	Fetch, Cancel, List Reply
	Capabilities        video.Capabilities
}
type FakeProvider struct {
	Server  *httptest.Server
	mu      sync.Mutex
	script  Script
	poll    int
	submits map[string]int
}

func NewFakeProvider(script Script) *FakeProvider {
	f := &FakeProvider{script: script, submits: make(map[string]int)}
	f.Server = httptest.NewServer(http.HandlerFunc(f.serve))
	return f
}
func (f *FakeProvider) Close() { f.Server.Close() }
func (f *FakeProvider) SubmitCount(token string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.submits[token]
}
func (f *FakeProvider) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	var reply Reply
	switch r.URL.Path {
	case "/submit":
		f.submits[r.Header.Get("X-Test-Correlation")]++
		reply = f.script.Submit
	case "/poll":
		if len(f.script.Poll) > 0 {
			i := f.poll
			if i >= len(f.script.Poll) {
				i = len(f.script.Poll) - 1
			}
			reply = f.script.Poll[i]
			f.poll++
		}
	case "/fetch":
		reply = f.script.Fetch
	case "/cancel":
		reply = f.script.Cancel
	case "/list":
		reply = f.script.List
	default:
		reply.Status = 404
	}
	f.mu.Unlock()
	if reply.Delay > 0 {
		timer := time.NewTimer(reply.Delay)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-r.Context().Done():
			return
		}
	}
	for k, v := range reply.Header {
		w.Header()[k] = append([]string(nil), v...)
	}
	status := reply.Status
	if status == 0 {
		status = 200
	}
	w.WriteHeader(status)
	_, _ = w.Write(reply.Body)
}
func (f *FakeProvider) Adapter() video.Adapter {
	c := f.script.Capabilities
	if c.Service == "" {
		c = video.Capabilities{Service: "fake", Revision: "fake-v1", CancelSupport: video.CancelNone, CorrelationLookup: video.LookupNone, OutputACL: video.ACLPrivateToken}
	}
	return &fakeAdapter{base: f.Server.URL, client: f.Server.Client(), caps: c}
}

type fakeAdapter struct {
	base   string
	client *http.Client
	caps   video.Capabilities
}

func (a *fakeAdapter) Capabilities() video.Capabilities { return a.caps }
func (a *fakeAdapter) Prepare(ctx context.Context, j *video.JobView) (video.Correlation, error) {
	if err := ctx.Err(); err != nil {
		return video.Correlation{}, err
	}
	return video.Correlation{Token: j.ID}, nil
}
func (a *fakeAdapter) call(ctx context.Context, path string, body any, token string) (*http.Response, error) {
	b, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, a.base+path, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Test-Correlation", token)
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		seconds, _ := strconv.Atoi(resp.Header.Get("Retry-After"))
		return nil, &video.UpstreamError{RetryAfter: time.Duration(seconds) * time.Second, Status: resp.StatusCode, Retryable: resp.StatusCode == 429 || resp.StatusCode >= 500, Body: b}
	}
	return resp, nil
}
func (a *fakeAdapter) decode(ctx context.Context, path string, body any, token string, out any) error {
	r, err := a.call(ctx, path, body, token)
	if err != nil {
		return err
	}
	defer r.Body.Close()
	if err := json.NewDecoder(r.Body).Decode(out); err != nil {
		return &video.SchemaError{Field: "body", Cause: err}
	}
	return nil
}
func (a *fakeAdapter) Submit(ctx context.Context, j *video.JobView, c video.Correlation) (v video.SubmitResult, err error) {
	err = a.decode(ctx, "/submit", j, c.Token, &v)
	if err == nil && !v.Normalized.Valid() {
		err = &video.SchemaError{Field: "status"}
	}
	return
}
func (a *fakeAdapter) Poll(ctx context.Context, ref video.ProviderRef) (v video.Observation, err error) {
	err = a.decode(ctx, "/poll", ref, "", &v)
	if err == nil && !v.Normalized.Valid() {
		err = &video.SchemaError{Field: "status"}
	}
	return
}
func (a *fakeAdapter) Fetch(ctx context.Context, ref video.ProviderRef, idx int) (io.ReadCloser, video.FetchMeta, error) {
	r, err := a.call(ctx, "/fetch", ref, strconv.Itoa(idx))
	if err != nil {
		return nil, video.FetchMeta{}, err
	}
	return r.Body, video.FetchMeta{ContentType: r.Header.Get("Content-Type"), Bytes: r.ContentLength}, nil
}
func (a *fakeAdapter) Cancel(ctx context.Context, ref video.ProviderRef) (v video.CancelResult, err error) {
	if a.caps.CancelSupport == video.CancelNone {
		return v, video.ErrCancelUnsupported
	}
	err = a.decode(ctx, "/cancel", ref, "", &v)
	return
}
func (a *fakeAdapter) Reconcile(ctx context.Context, j *video.JobView, c video.Correlation) (v video.ReconcileResult, err error) {
	if a.caps.CorrelationLookup == video.LookupNone {
		return v, video.ErrReconcileUnsupported
	}
	err = a.decode(ctx, "/list", j, c.Token, &v)
	if err == nil && v.Found && v.ProvenAbsent {
		err = &video.SchemaError{Field: "reconcile", Cause: fmt.Errorf("contradictory result")}
	}
	return
}
