// Package byteplus implements the pinned ModelArk Seedance task API.
package byteplus

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
)

type Config struct {
	Provider          config.VideoProviderConfig
	CorrelationSecret string
	FetchTimeout      time.Duration
	MaxDownloadBytes  int64
	// Batch 3 supplies a shared, account-scoped Redis limiter across replicas.
	// Without one the adapter still enforces a process-local 1 QPS list limit.
	ListLimiter func(context.Context, string) error
}
type Adapter struct {
	cfg              Config
	base             *url.URL
	api, download    *http.Client
	maxDownloadBytes int64
	pageSize         int
	limiter          *listLimiter
	mu               sync.Mutex
	downloads        map[string]downloadCount
}
type downloadCount struct {
	count   int
	expires time.Time
}

var _ video.Adapter = (*Adapter)(nil)

func New(c Config) (*Adapter, error) {
	if c.Provider.APIKey == "" || c.CorrelationSecret == "" {
		return nil, fmt.Errorf("byteplus credentials and correlation secret required")
	}
	if c.Provider.BaseURL == "" {
		c.Provider.BaseURL = "https://ark.ap-southeast.bytepluses.com/api/v3"
	}
	if e := media.ValidateURL(c.Provider.BaseURL); e != nil {
		return nil, e
	}
	u, _ := url.Parse(c.Provider.BaseURL)
	if u.RawQuery != "" {
		return nil, fmt.Errorf("byteplus base URL must not contain a query")
	}
	if c.Provider.Region == "" {
		c.Provider.Region = "ap-southeast-1"
	}
	if c.Provider.Region != "ap-southeast-1" {
		return nil, fmt.Errorf("unsupported byteplus region")
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
	if c.Provider.ExecutionExpiresAfter == 0 {
		c.Provider.ExecutionExpiresAfter = 172800
	}
	if c.Provider.ExecutionExpiresAfter < 3600 || c.Provider.ExecutionExpiresAfter > 259200 || time.Duration(c.Provider.ExecutionExpiresAfter)*time.Second < d.Run {
		return nil, fmt.Errorf("invalid byteplus execution expiry")
	}
	for _, m := range c.Provider.Models {
		if _, err := capability.NewRegistry().Model("byteplus", m); err != nil {
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
	return &Adapter{cfg: c, base: u, api: api, download: newDownloadClient(*d), maxDownloadBytes: c.MaxDownloadBytes, pageSize: 100, limiter: &listLimiter{}, downloads: make(map[string]downloadCount)}, nil
}
func newDownloadClient(d config.VideoProviderDeadlines) *http.Client {
	return media.NewHTTPClient(d.Connect, d.Read)
}
func (a *Adapter) Capabilities() video.Capabilities { return capability.BytePlus() }
func (a *Adapter) Prepare(ctx context.Context, j *video.JobView) (video.Correlation, error) {
	if e := ctx.Err(); e != nil {
		return video.Correlation{}, e
	}
	if j == nil || j.OrgID == "" || j.KeyID == "" {
		return video.Correlation{}, fmt.Errorf("video correlation requires org and key identity")
	}
	endUser := j.Request.EndUserID
	if endUser == "" {
		endUser = j.EndUserHash
	}
	h := hmac.New(sha256.New, []byte(a.cfg.CorrelationSecret))
	h.Write([]byte(j.OrgID + j.KeyID + endUser))
	token := hex.EncodeToString(h.Sum(nil))
	return video.Correlation{Token: token, Data: map[string]string{"safety_identifier": token}}, nil
}
func (a *Adapter) checkCorrelation(ctx context.Context, j *video.JobView, c video.Correlation) error {
	want, e := a.Prepare(ctx, j)
	if e != nil {
		return e
	}
	if !hmac.Equal([]byte(want.Token), []byte(c.Token)) {
		return fmt.Errorf("video correlation mismatch")
	}
	return nil
}
func (a *Adapter) Submit(ctx context.Context, j *video.JobView, c video.Correlation) (video.SubmitResult, error) {
	if e := a.checkCorrelation(ctx, j, c); e != nil {
		return video.SubmitResult{}, e
	}
	payload, e := a.submitBody(j, c.Token)
	if e != nil {
		return video.SubmitResult{}, e
	}
	var receipt struct {
		ID string `json:"id"`
	}
	raw, h, e := a.call(ctx, http.MethodPost, "", nil, payload, a.cfg.Provider.Deadlines.Submit, &receipt)
	if e != nil {
		return video.SubmitResult{}, e
	}
	if !validID(receipt.ID) {
		return video.SubmitResult{}, &video.SchemaError{Field: "id"}
	}
	return video.SubmitResult{ProviderJobID: receipt.ID, ProviderState: "queued", Normalized: video.StateQueued, ProviderRequestID: requestID(h), Raw: raw}, nil
}
func (a *Adapter) Poll(ctx context.Context, ref video.ProviderRef) (video.Observation, error) {
	if !validID(ref.ProviderJobID) {
		return video.Observation{}, &video.SchemaError{Field: "provider_job_id"}
	}
	var task taskResponse
	raw, h, e := a.call(ctx, http.MethodGet, ref.ProviderJobID, nil, nil, a.cfg.Provider.Deadlines.Read, &task)
	if e != nil {
		return video.Observation{}, e
	}
	obs, e := task.observation()
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
func (a *Adapter) Cancel(ctx context.Context, ref video.ProviderRef) (video.CancelResult, error) {
	unavailable := video.CancelResult{State: video.CancelNotAvailable}
	if ref.ProviderState != "" && ref.ProviderState != "queued" {
		return unavailable, &video.UpstreamError{Status: http.StatusConflict, Code: "cancel_not_available"}
	}
	obs, e := a.Poll(ctx, ref)
	if e != nil {
		return video.CancelResult{}, e
	}
	if obs.Normalized != video.StateQueued {
		return unavailable, &video.UpstreamError{Status: http.StatusConflict, Code: "cancel_not_available"}
	}
	if _, _, e = a.call(ctx, http.MethodDelete, ref.ProviderJobID, nil, nil, a.cfg.Provider.Deadlines.Read, nil); e != nil {
		return video.CancelResult{}, e
	}
	obs, e = a.Poll(ctx, ref)
	if e != nil {
		return video.CancelResult{State: video.CancelRequested}, e
	}
	switch obs.Normalized {
	case video.StateCancelled:
		return video.CancelResult{State: video.CancelConfirmed}, nil
	case video.StateQueued:
		return video.CancelResult{State: video.CancelRequested}, nil
	default:
		return unavailable, &video.UpstreamError{Status: http.StatusConflict, Code: "cancel_not_available"}
	}
}
func (a *Adapter) Fetch(ctx context.Context, ref video.ProviderRef, idx int) (io.ReadCloser, video.FetchMeta, error) {
	if idx < 0 {
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "artifact_index"}
	}
	if len(ref.Outputs) == 0 {
		obs, e := a.Poll(ctx, ref)
		if e != nil {
			return nil, video.FetchMeta{}, e
		}
		if obs.Normalized != video.StateCompleted {
			return nil, video.FetchMeta{}, fmt.Errorf("video output not ready")
		}
		ref.Outputs = obs.Outputs
	}
	if idx >= len(ref.Outputs) {
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "artifact_index"}
	}
	out := ref.Outputs[idx]
	if e := media.ValidateURL(out.URL); e != nil {
		return nil, video.FetchMeta{}, e
	}
	if out.ExpiresAt != nil && !time.Now().Before(*out.ExpiresAt) {
		return nil, video.FetchMeta{}, &video.UpstreamError{Status: 410, Code: "output_expired"}
	}
	ctx, cancel := context.WithTimeout(ctx, a.cfg.FetchTimeout)
	req, e := http.NewRequestWithContext(ctx, http.MethodGet, out.URL, nil)
	if e != nil {
		cancel()
		return nil, video.FetchMeta{}, &video.SchemaError{Field: "output.url"}
	}
	// Count attempts conservatively. Limits are local hints; the lifecycle must
	// persist attempts across restarts/replicas before scheduling further copies.
	if e = a.countDownload(ref.ProviderJobID, idx); e != nil {
		cancel()
		return nil, video.FetchMeta{}, e
	}
	resp, e := a.download.Do(req)
	if e != nil {
		classified := transportError(ctx, e)
		cancel()
		return nil, video.FetchMeta{}, classified
	}
	if resp.StatusCode != 200 {
		defer resp.Body.Close()
		cancel()
		return nil, video.FetchMeta{}, upstreamError(resp, nil, false)
	}
	if resp.ContentLength > a.maxDownloadBytes {
		resp.Body.Close()
		cancel()
		return nil, video.FetchMeta{}, fmt.Errorf("video output exceeds byte cap")
	}
	meta := video.FetchMeta{ContentType: out.ContentType, Bytes: resp.ContentLength, Width: out.Width, Height: out.Height, DurationSeconds: out.DurationSeconds}
	if meta.ContentType == "" {
		meta.ContentType = resp.Header.Get("Content-Type")
	}
	return &downloadBody{ReadCloser: resp.Body, cancel: cancel, left: a.maxDownloadBytes}, meta, nil
}
func (a *Adapter) countDownload(id string, idx int) error {
	if !validID(id) {
		return &video.SchemaError{Field: "provider_job_id"}
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	now := time.Now()
	for k, v := range a.downloads {
		if !now.Before(v.expires) {
			delete(a.downloads, k)
		}
	}
	key := fmt.Sprintf("%s/%d", id, idx)
	v := a.downloads[key]
	if v.count >= 100 {
		return fmt.Errorf("provider output download limit reached")
	}
	if v.count == 0 {
		v.expires = now.Add(24 * time.Hour)
	}
	v.count++
	a.downloads[key] = v
	return nil
}
func (a *Adapter) DownloadAttempts(id string, idx int) int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.downloads[fmt.Sprintf("%s/%d", id, idx)].count
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
