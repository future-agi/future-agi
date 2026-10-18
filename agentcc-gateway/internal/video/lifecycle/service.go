// Package lifecycle owns the durable, leased video workflow. HTTP handlers only
// validate identity, run the pipeline and accept work; they never submit upstream.
package lifecycle

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
	"github.com/futureagi/agentcc-gateway/internal/video/tariff"
	"github.com/redis/go-redis/v9"
)

type Options struct {
	Config      config.VideoConfig
	Store       video.Store
	Redis       *redisstate.Client
	Budget      *redisstate.BudgetStore
	Adapters    *provider.Registry
	Artifacts   artifacts.Store
	Engine      *pipeline.Engine
	Clock       Clock
	Hooks       Hooks
	Prefix      string
	DeniedHosts []string
}
type Service struct {
	metrics          *metrics.Registry
	exporter         otel.SpanExporter
	telemetryService string
	cfg              config.VideoConfig
	store            video.Store
	redis            *redisstate.Client
	budget           *redisstate.BudgetStore
	adapters         *provider.Registry
	blobs            artifacts.Store
	engine           *pipeline.Engine
	clock            Clock
	hooks            Hooks
	prefix           string
	limiter          *redisstate.RateLimiter
	sweeper          *artifacts.Sweeper
	deniedHosts      []string
}

func New(o Options) (*Service, error) {
	if o.Store == nil || o.Adapters == nil || o.Artifacts == nil {
		return nil, errors.New("video lifecycle dependencies required")
	}
	if o.Config.Submit.LeaseTTL <= 0 || o.Config.Poll.MaxConcurrentTotal <= 0 {
		return nil, errors.New("video lifecycle configuration required")
	}
	if o.Clock == nil {
		o.Clock = realClock{}
	}
	if o.Prefix == "" {
		o.Prefix = "video:v1:"
	}
	s := &Service{cfg: o.Config, store: o.Store, redis: o.Redis, budget: o.Budget, adapters: o.Adapters, blobs: o.Artifacts, engine: o.Engine, clock: o.Clock, hooks: o.Hooks, prefix: o.Prefix, deniedHosts: append([]string(nil), o.DeniedHosts...)}
	s.limiter = redisstate.NewRateLimiter(o.Redis, nil, o.Prefix+"ratelimit:")
	if o.Redis != nil {
		s.sweeper = artifacts.NewSweeper(o.Redis, o.Artifacts, o.Prefix+"artifacts:")
	}
	return s, nil
}
func api(status int, code string) *models.APIError {
	typ := models.ErrTypeInvalidRequest
	if status >= 500 {
		typ = models.ErrTypeServer
	}
	if status == 429 {
		typ = models.ErrTypeRateLimit
	}
	return &models.APIError{Status: status, Type: typ, Code: code, Message: strings.ReplaceAll(code, "_", " ")}
}

// APIError exposes safe fixed messages, never provider bodies, URLs or credentials.
func APIError(err error) *models.APIError {
	var ae *models.APIError
	if errors.As(err, &ae) {
		return ae
	}
	var ve *capability.ValidationError
	if errors.As(err, &ve) {
		return ve.APIError()
	}
	var me *media.Error
	if errors.As(err, &me) {
		return api(me.Status, me.Code)
	}
	switch {
	case errors.Is(err, video.ErrVideoJobNotFound):
		return api(404, "video_not_found")
	case errors.Is(err, video.ErrIdempotencyConflict):
		return api(409, "idempotency_conflict")
	case errors.Is(err, video.ErrDeletedJob):
		return api(410, "deleted_job")
	case errors.Is(err, video.ErrBudgetExceeded):
		return api(429, "budget_exceeded")
	case errors.Is(err, video.ErrActiveLimit):
		return api(429, "rate_limit_exceeded")
	case errors.Is(err, video.ErrLeaseHeld):
		return api(409, "video_busy")
	case errors.Is(err, artifacts.ErrRange):
		return api(416, "invalid_range")
	default:
		return api(503, "video_store_unavailable")
	}
}
func (s *Service) SetKillSwitch(ctx context.Context, on bool) error {
	if s.redis == nil {
		return video.ErrStoreUnavailable
	}
	v := "0"
	if on {
		v = "1"
	}
	if err := s.redis.Do(func(c redis.UniversalClient) error { return c.Set(ctx, s.prefix+"killswitch", v, 0).Err() }); err != nil {
		return video.ErrStoreUnavailable
	}
	return nil
}

// InitializeKillSwitch is called once at startup. Runtime reads use Redis only.
func (s *Service) InitializeKillSwitch(ctx context.Context) error {
	if s.redis == nil {
		return video.ErrStoreUnavailable
	}
	value := "0"
	if s.cfg.KillSwitch {
		value = "1"
	}
	if e := s.redis.Do(func(c redis.UniversalClient) error { return c.SetNX(ctx, s.prefix+"killswitch", value, 0).Err() }); e != nil {
		return video.ErrStoreUnavailable
	}
	return nil
}
func (s *Service) Killed(ctx context.Context) (bool, error) {
	if s.redis == nil {
		if s.cfg.Store == "memory" {
			return s.cfg.KillSwitch, nil
		}
		return false, video.ErrStoreUnavailable
	}
	var v string
	err := s.redis.Do(func(c redis.UniversalClient) error {
		var e error
		v, e = c.Get(ctx, s.prefix+"killswitch").Result()
		if errors.Is(e, redis.Nil) {
			return nil
		}
		return e
	})
	if err != nil {
		return false, video.ErrStoreUnavailable
	}
	return v == "1" || v == "true", nil
}
func (s *Service) Accept(ctx context.Context, rc *models.RequestContext, req provider.Request, idem string, scopes []video.BudgetReservation) (video.AcceptResult, error) {
	fail := func(e error) (video.AcceptResult, error) { return video.AcceptResult{}, e }
	org := rc.Metadata["org_id"]
	if org == "" {
		return fail(api(403, "missing_org"))
	}
	if len(idem) < 1 || len(idem) > 255 {
		return fail(api(400, "missing_idempotency_key"))
	}
	for _, c := range idem {
		if c < 32 || c > 126 {
			return fail(api(400, "invalid_idempotency_key"))
		}
	}
	if rc.Metadata["key_type"] == "managed" {
		return fail(api(503, "managed_tariff_not_configured"))
	}
	killed, err := s.Killed(ctx)
	if err != nil {
		return fail(err)
	}
	if killed {
		return fail(api(503, "video_submissions_disabled"))
	}
	service, _, _ := strings.Cut(req.Model, "/")
	pc := s.cfg.Providers[service]
	resolved, err := capability.NewRegistry().Normalize(req, pc.Region)
	if err != nil {
		return fail(err)
	}
	if !pc.Enabled || !slices.Contains(pc.Models, resolved.ModelID) {
		return fail(api(503, "provider_not_configured"))
	}
	if _, ok := s.adapters.Get(service); !ok {
		return fail(api(503, "provider_not_configured"))
	}
	if allowed := rc.Metadata["auth_allowed_providers"]; allowed != "" && !slices.Contains(strings.Split(allowed, ","), service) {
		return fail(api(403, "provider_not_allowed"))
	}
	if s.redis != nil {
		allowed, retry, e := s.limiter.AllowWindow(ctx, accountKey("org", org, "")+":accept", s.cfg.Limits.OrgSubmitRPM, time.Minute)
		if e != nil {
			return fail(video.ErrStoreUnavailable)
		}
		if !allowed {
			rc.Metadata["ratelimit_reset"] = strconv.Itoa(max(1, int(math.Ceil(retry.Seconds()))))
			return fail(api(429, "rate_limit_exceeded"))
		}
	}
	id := video.NewID()
	now := s.clock.Now()
	expires := now.Add(s.cfg.Retention.JobMetadata)
	ingress, err := s.ingress(ctx, id, &resolved, expires)
	if err != nil {
		return fail(err)
	}
	keep := false
	defer func() {
		if !keep {
			cleanup, stop := context.WithTimeout(context.Background(), 5*time.Second)
			defer stop()
			for _, in := range ingress {
				_ = s.blobs.Delete(cleanup, in.Key)
			}
		}
	}()
	_, fp, err := capability.Fingerprint(resolved)
	if err != nil {
		return fail(err)
	}
	estimate, err := tariff.Estimate(resolved, tariff.Options{MinTokensWithVideoInput: pc.MinTokensWithVideoInput})
	priced := err == nil && pc.TariffRevision == tariff.Revision
	if !priced && !pc.AllowUnpricedModels {
		return fail(api(503, "tariff_missing"))
	}
	r := resolved.Request
	for i := range r.Inputs {
		r.Inputs[i].Source = provider.Source{}
	}
	endHash := ""
	if r.EndUserID != "" {
		h := sha256.Sum256([]byte(r.EndUserID))
		endHash = hex.EncodeToString(h[:])
		r.EndUserID = ""
	}
	canonical, err := json.Marshal(r)
	if err != nil {
		return fail(err)
	}
	j := &video.VideoJob{ID: id, OrgID: org, KeyID: rc.Metadata["auth_key_id"], KeyType: rc.Metadata["key_type"], Creator: rc.Metadata["auth_key_owner"], Model: req.Model, Service: service, ModelID: resolved.ModelID, Region: resolved.Region, AccountRef: pc.AccountRef, CapabilityRevision: resolved.CapabilityRevision, RequestCanonical: canonical, Fingerprint: fp, Ingress: ingress, EndUserHash: endHash, CreatedAt: now, ExpiresAt: expires, SubmitBy: now.Add(s.cfg.Submit.Deadline), RetrySafe: true, CancelState: "none", ReconcileState: video.ReconcileNone, ClientMetadata: r.Metadata, TraceID: rc.TraceID}
	j.StateChangedAt = now
	j.Status = video.StatusSubmitting
	defer s.span("video.submit", j)()
	if priced {
		j.EstimateUnit = estimate.Unit
		j.EstimateQty = estimate.Quantity
		j.EstimateUSD = estimate.USD
		j.ReservedMicros = estimate.Micros
		j.TariffRevision = estimate.TariffRevision
		hasVideo := false
		for _, in := range resolved.Request.Inputs {
			hasVideo = hasVideo || in.Role == provider.ReferenceVideo
		}
		rate, e := tariff.BytePlusRate(resolved.ModelID, r.Resolution, hasVideo, r.Audio != nil && *r.Audio)
		if e != nil {
			return fail(e)
		}
		j.RateUSDPerMillion = rate.USDPerMillion
	} else {
		j.SettlementState = video.SettlementUnsettled
	}
	result, err := s.store.Accept(ctx, video.AcceptRequest{Job: j, Operation: string(resolved.Operation), IdempotencyKey: idem, Reservations: scopes, OrgMaxActive: s.cfg.Limits.OrgMaxActiveJobs, AccountMaxActive: pc.Limits.MaxActiveTasks})
	if err != nil {
		keep = errors.Is(err, video.ErrReservationUncertain)
		return fail(err)
	}
	keep = !result.Replay
	if result.Replay {
		j.ID = result.Job.ID
		j.TraceID = result.Job.TraceID
		j.TraceParentID = result.Job.TraceParentID
		j.Status = result.Job.Status
		j.ProviderJobID = result.Job.ProviderJobID
	}
	if !result.Replay {
		s.event("video_jobs_total", result.Job, "")
	}
	rc.Metadata["video_id"] = result.Job.ID
	rc.TraceID = result.Job.TraceID
	return result, nil
}
func (s *Service) ingress(ctx context.Context, id string, res *capability.Resolved, expiry time.Time) (out []video.InputAsset, err error) {
	ctx, cancel := context.WithTimeout(ctx, s.cfg.Media.TotalIngressTimeout)
	defer cancel()
	defer func() {
		if err != nil {
			cleanup, stop := context.WithTimeout(context.Background(), time.Second)
			defer stop()
			for _, v := range out {
				_ = s.blobs.Delete(cleanup, v.Key)
			}
		}
	}()
	var total int64
	for i := range res.Request.Inputs {
		in := &res.Request.Inputs[i]
		spec := res.Capability.Inputs[in.Role]
		key := s.blobKey(id, fmt.Sprintf("input-%d", i))
		sink := func(c context.Context, k string, r io.Reader) error {
			_, e := s.blobs.Put(c, k, r, artifacts.Meta{ContentType: in.MediaType, ExpiresAt: expiry})
			return e
		}
		fetch := media.NewFetcher(sink)
		fetch.DeniedHosts = s.deniedHosts
		l := media.Limits{MaxBytes: spec.MaxBytes, Deadline: s.cfg.Media.FetchTimeout, MaxPixels: min(spec.MaxPixels, s.cfg.Media.MaxDecodedPixels), MinDimension: spec.MinDimension, MaxDimension: spec.MaxDimension, MinRatio: spec.MinRatio, MaxRatio: spec.MaxRatio}
		var v media.Result
		if in.Source.Data != "" {
			if base64.StdEncoding.DecodedLen(len(in.Source.Data)) > int(s.cfg.Media.MaxInlineBytes) {
				return out, api(413, "media_too_large")
			}
			v, err = fetch.Verify(ctx, base64.NewDecoder(base64.StdEncoding, strings.NewReader(in.Source.Data)), in.MediaType, key, l)
		} else {
			v, err = fetch.Fetch(ctx, in.Source.URL, in.MediaType, key, l)
		}
		if err != nil {
			return out, err
		}
		out = append(out, video.InputAsset{Key: key, Digest: v.Digest, Bytes: v.Bytes, Width: v.Width, Height: v.Height, DurationSeconds: v.DurationSeconds})
		total += v.Bytes
		if total > 64<<20 {
			return out, api(413, "media_too_large")
		}
		in.Digest = v.Digest
		in.Bytes = v.Bytes
		in.Width = v.Width
		in.Height = v.Height
		in.DurationSeconds = v.DurationSeconds
		if s.sweeper != nil {
			if err = s.sweeper.Track(ctx, key, expiry); err != nil {
				return out, err
			}
		}
	}
	return out, nil
}
func (s *Service) blobKey(id, suffix string) string {
	prefix := "video/"
	if s.cfg.Artifacts.Backend == "s3" {
		prefix = s.cfg.Artifacts.S3.Prefix
	}
	return strings.TrimSuffix(prefix, "/") + "/" + id + "/" + suffix
}
func (s *Service) view(ctx context.Context, j *video.VideoJob, inputs bool) (*provider.JobView, error) {
	v := &provider.JobView{ID: j.ID, OrgID: j.OrgID, KeyID: j.KeyID, Service: j.Service, ModelID: j.ModelID, Region: j.Region, AccountRef: j.AccountRef, EndUserHash: j.EndUserHash, AttemptID: j.AttemptID, AttemptStartedAt: j.AttemptStartedAt, SubmitBy: j.SubmitBy}
	if len(j.RequestCanonical) > 0 {
		if err := json.Unmarshal(j.RequestCanonical, &v.Request); err != nil {
			return nil, err
		}
	}
	if inputs {
		for i, in := range j.Ingress {
			if i >= len(v.Request.Inputs) {
				return nil, errors.New("invalid persisted input")
			}
			obj, e := s.blobs.Open(ctx, in.Key, "")
			if e != nil {
				return nil, e
			}
			b, e := io.ReadAll(io.LimitReader(obj.Body, in.Bytes+1))
			obj.Body.Close()
			if e != nil || int64(len(b)) != in.Bytes {
				return nil, errors.New("stored input unavailable")
			}
			sum := sha256.Sum256(b)
			if hex.EncodeToString(sum[:]) != in.Digest {
				return nil, errors.New("stored input digest mismatch")
			}
			v.Request.Inputs[i].Source.Data = base64.StdEncoding.EncodeToString(b)
			v.Request.Inputs[i].Bytes = in.Bytes
			v.Request.Inputs[i].Width = in.Width
			v.Request.Inputs[i].Height = in.Height
			v.Request.Inputs[i].DurationSeconds = in.DurationSeconds
		}
	}
	return v, nil
}
func ref(j *video.VideoJob) provider.ProviderRef {
	r := provider.ProviderRef{JobID: j.ID, Service: j.Service, ModelID: j.ModelID, Region: j.Region, AccountRef: j.AccountRef, ProviderJobID: j.ProviderJobID, ProviderState: j.ProviderState}
	if j.Observation != nil {
		r.Outputs = j.Observation.Outputs
	}
	return r
}

// Close releases the artifact backend after workers and HTTP streams drain.
func (s *Service) Close() error {
	if c, ok := s.blobs.(io.Closer); ok {
		return c.Close()
	}
	return nil
}

// adapterFor refuses credential/account rebinding after a restart. Disabled
// providers retain their adapter for polling, cancellation and settlement.
func (s *Service) adapterFor(j *video.VideoJob) (provider.Adapter, bool) {
	a, ok := s.adapters.Get(j.Service)
	if !ok {
		return nil, false
	}
	p, ok := s.cfg.Providers[j.Service]
	if !ok || p.AccountRef != j.AccountRef {
		return nil, false
	}
	region := p.Region
	if region == "" {
		regions := a.Capabilities().Regions
		if len(regions) > 0 {
			region = regions[0]
		}
	}
	if region != j.Region {
		return nil, false
	}
	return a, true
}
