// Package openrouter implements the OpenRouter video generation API
// (POST /videos, GET /videos/{id}, GET /videos/{id}/content). OpenRouter is an
// aggregator: a successful job proves the OpenRouter route for that model, not
// the model vendor's native API, credentials, billing or retention.
package openrouter

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
)

const (
	DefaultBaseURL = "https://openrouter.ai/api/v1"
	service        = "openrouter"
	region         = "global"
)

type Config struct {
	Provider          config.VideoProviderConfig
	CorrelationSecret string
	FetchTimeout      time.Duration
	MaxDownloadBytes  int64
}
type Adapter struct {
	cfg              Config
	base             *url.URL
	api, download    *http.Client
	maxDownloadBytes int64
}

var _ video.Adapter = (*Adapter)(nil)

func New(c Config) (*Adapter, error) {
	if c.Provider.APIKey == "" {
		return nil, fmt.Errorf("openrouter credentials required")
	}
	if c.Provider.SafetyIdentifierMode == "org_key_hmac" && c.CorrelationSecret == "" {
		return nil, fmt.Errorf("openrouter correlation secret required for org_key_hmac")
	}
	if c.Provider.BaseURL == "" {
		c.Provider.BaseURL = DefaultBaseURL
	}
	if e := media.ValidateURL(c.Provider.BaseURL); e != nil {
		return nil, e
	}
	u, _ := url.Parse(c.Provider.BaseURL)
	if u.RawQuery != "" {
		return nil, fmt.Errorf("openrouter base URL must not contain a query")
	}
	if c.Provider.Region == "" {
		c.Provider.Region = region
	}
	if c.Provider.Region != region {
		return nil, fmt.Errorf("unsupported openrouter region")
	}
	d := &c.Provider.Deadlines
	if d.Connect <= 0 {
		d.Connect = 5 * time.Second
	}
	if d.Read <= 0 {
		d.Read = 30 * time.Second
	}
	if d.Submit <= 0 {
		d.Submit = 30 * time.Second
	}
	if d.Reconcile <= 0 {
		d.Reconcile = 60 * time.Second
	}
	for _, m := range c.Provider.Models {
		if _, err := capability.NewRegistry().Model(service, m); err != nil {
			return nil, err
		}
	}
	if c.FetchTimeout <= 0 {
		c.FetchTimeout = 10 * time.Minute
	}
	if c.MaxDownloadBytes <= 0 {
		c.MaxDownloadBytes = 2 << 30
	}
	api := media.NewHTTPClient(d.Connect, d.Read)
	api.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	// Content downloads follow at most a few validated redirects; the media
	// client strips Authorization from every redirected request, so the API key
	// is only ever sent to the configured OpenRouter origin.
	return &Adapter{cfg: c, base: u, api: api, download: media.NewHTTPClient(d.Connect, d.Read), maxDownloadBytes: c.MaxDownloadBytes}, nil
}
func (a *Adapter) Capabilities() video.Capabilities { return capability.OpenRouter() }

// Prepare derives an optional end-user identifier. OpenRouter records `user`
// for observability only; it is not a deduplication key.
func (a *Adapter) Prepare(ctx context.Context, j *video.JobView) (video.Correlation, error) {
	if e := ctx.Err(); e != nil {
		return video.Correlation{}, e
	}
	if j == nil || j.OrgID == "" || j.KeyID == "" {
		return video.Correlation{}, fmt.Errorf("video correlation requires org and key identity")
	}
	if a.cfg.Provider.SafetyIdentifierMode != "org_key_hmac" {
		return video.Correlation{}, nil
	}
	endUser := j.Request.EndUserID
	if endUser == "" {
		endUser = j.EndUserHash
	}
	h := hmac.New(sha256.New, []byte(a.cfg.CorrelationSecret))
	h.Write([]byte(j.OrgID + j.KeyID + endUser))
	token := hex.EncodeToString(h.Sum(nil))
	return video.Correlation{Token: token, Data: map[string]string{"user": token}}, nil
}
func (a *Adapter) Submit(ctx context.Context, j *video.JobView, c video.Correlation) (video.SubmitResult, error) {
	want, e := a.Prepare(ctx, j)
	if e != nil {
		return video.SubmitResult{}, e
	}
	if !hmac.Equal([]byte(want.Token), []byte(c.Token)) {
		return video.SubmitResult{}, fmt.Errorf("video correlation mismatch")
	}
	payload, e := a.submitBody(j, c.Token)
	if e != nil {
		return video.SubmitResult{}, e
	}
	var receipt jobResponse
	raw, h, e := a.call(ctx, http.MethodPost, "", nil, payload, a.cfg.Provider.Deadlines.Submit, &receipt)
	if e != nil {
		return video.SubmitResult{}, e
	}
	if !validID(receipt.ID) {
		return video.SubmitResult{}, &video.SchemaError{Field: "id"}
	}
	state, ok := normalize(receipt.Status)
	if !ok {
		return video.SubmitResult{}, &video.SchemaError{Field: "status"}
	}
	// Terminal states are established by polling, never from the receipt.
	if state != video.StateRunning {
		state = video.StateQueued
	}
	return video.SubmitResult{ProviderJobID: receipt.ID, ProviderState: receipt.Status, Normalized: state, ProviderRequestID: requestID(h), Raw: raw}, nil
}
func (a *Adapter) Poll(ctx context.Context, ref video.ProviderRef) (video.Observation, error) {
	if !validID(ref.ProviderJobID) {
		return video.Observation{}, &video.SchemaError{Field: "provider_job_id"}
	}
	var job jobResponse
	raw, h, e := a.call(ctx, http.MethodGet, ref.ProviderJobID, nil, nil, a.cfg.Provider.Deadlines.Read, &job)
	if e != nil {
		return video.Observation{}, e
	}
	obs, e := job.observation(ref.ProviderJobID)
	if e != nil {
		return obs, e
	}
	obs.Raw = raw
	if d := retryAfter(h.Get("Retry-After"), time.Now()); d > 0 {
		obs.RetryAfter = &d
	}
	if obs.Error != nil {
		obs.Error.RequestID = requestID(h)
	}
	return obs, nil
}

// Cancel is unsupported: OpenRouter documents no cancel operation. Local
// deletion never implies that upstream generation or billing stopped.
func (a *Adapter) Cancel(context.Context, video.ProviderRef) (video.CancelResult, error) {
	return video.CancelResult{State: video.CancelNotAvailable}, video.ErrCancelUnsupported
}

// Reconcile is unsupported: there is no lookup by client reference, so an
// uncertain submit stays submission_unknown until an operator attaches the
// provider job id or the reconciliation window ends unresolved.
func (a *Adapter) Reconcile(context.Context, *video.JobView, video.Correlation) (video.ReconcileResult, error) {
	return video.ReconcileResult{}, video.ErrReconcileUnsupported
}

// Fetch streams output bytes from the authenticated content endpoint built from
// the configured base URL and the provider job id. Provider-supplied URLs,
// including signed bearer download URLs, are never dialled or persisted.
func (a *Adapter) Fetch(ctx context.Context, ref video.ProviderRef, idx int) (io.ReadCloser, video.FetchMeta, error) {
	if idx < 0 {
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "artifact_index"}
	}
	if !validID(ref.ProviderJobID) {
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "provider_job_id"}
	}
	if len(ref.Outputs) > 0 && idx >= len(ref.Outputs) {
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "artifact_index"}
	}
	ctx, cancel := context.WithTimeout(ctx, a.cfg.FetchTimeout)
	u := a.endpoint(ref.ProviderJobID + "/content")
	u.RawQuery = url.Values{"index": {strconv.Itoa(idx)}}.Encode()
	req, e := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if e != nil {
		cancel()
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "content.url"}
	}
	req.Header.Set("Authorization", "Bearer "+a.cfg.Provider.APIKey)
	req.Header.Set("Accept", "video/*")
	resp, e := a.download.Do(req)
	if e != nil {
		classified := transportError(ctx, e)
		cancel()
		return nil, video.FetchMeta{}, classified
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
		resp.Body.Close()
		cancel()
		return nil, video.FetchMeta{}, upstreamError(resp, body, false)
	}
	if resp.ContentLength > a.maxDownloadBytes {
		resp.Body.Close()
		cancel()
		return nil, video.FetchMeta{}, fmt.Errorf("video output exceeds byte cap")
	}
	meta := video.FetchMeta{ContentType: resp.Header.Get("Content-Type"), Bytes: resp.ContentLength}
	if len(ref.Outputs) > idx {
		out := ref.Outputs[idx]
		meta.Width, meta.Height, meta.DurationSeconds = out.Width, out.Height, out.DurationSeconds
	}
	return &downloadBody{ReadCloser: resp.Body, cancel: cancel, left: a.maxDownloadBytes}, meta, nil
}

type downloadBody struct {
	io.ReadCloser
	cancel context.CancelFunc
	left   int64
}

func (b *downloadBody) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return 0, nil
	}
	if b.left == 0 {
		var probe [1]byte
		n, e := b.ReadCloser.Read(probe[:])
		if n > 0 {
			return 0, fmt.Errorf("video output exceeds byte cap")
		}
		return 0, e
	}
	if int64(len(p)) > b.left {
		p = p[:b.left]
	}
	n, e := b.ReadCloser.Read(p)
	b.left -= int64(n)
	return n, e
}
func (b *downloadBody) Close() error { b.cancel(); return b.ReadCloser.Close() }
