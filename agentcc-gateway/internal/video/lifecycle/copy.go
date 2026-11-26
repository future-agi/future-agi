package lifecycle

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path"
	"strings"
	"time"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
)

func (s *Service) copyResult(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	if j.DeletedAt != nil || j.IsTerminal() {
		if !j.IsTerminal() {
			j.Status = video.StatusCompleted
			now := s.clock.Now()
			j.CompletedAt = &now
		}
		if e := s.save(ctx, j, l); e != nil {
			return e
		}
		return s.settle(ctx, j, l)
	}
	if len(j.Artifacts) == 0 {
		n := 1
		if j.Observation != nil {
			n = max(n, len(j.Observation.Outputs))
		}
		for i := 0; i < n; i++ {
			j.Artifacts = append(j.Artifacts, video.Artifact{Index: i, State: video.ArtifactPending, BlobKey: s.blobKey(j.ID, fmt.Sprintf("output-%d.mp4", i)), ExpiresAt: s.clock.Now().Add(s.cfg.Artifacts.TTL)})
		}
	}
	if j.CopyAttempts >= 3 || !s.clock.Now().Before(j.CopyBy) || j.DownloadAttempts >= 100 {
		return s.finishCopy(ctx, j, l, false)
	}
	a, ok := s.adapterFor(j)
	if !ok {
		return s.saveLater(ctx, j, l, time.Second)
	}
	err := s.withPermit(ctx, j, "copy", func(ctx context.Context) error {
		defer s.span("video.result.copy", j)()
		ctx, cancel := context.WithTimeout(ctx, max(time.Millisecond, j.CopyBy.Sub(s.clock.Now())))
		defer cancel()
		j.CopyAttempts++
		if e := s.save(ctx, j, l); e != nil {
			return e
		}
		for i := range j.Artifacts {
			art := &j.Artifacts[i]
			if art.State == video.ArtifactAvailable {
				continue
			}
			art.State = video.ArtifactCopying
			j.DownloadAttempts++
			if e := s.save(ctx, j, l); e != nil {
				return e
			}
			if e := s.hit("copy.before"); e != nil {
				return e
			}
			// Track before Put as well as after commit, so a crash never leaks a blob.
			if s.sweeper != nil {
				if e := s.sweeper.Track(ctx, art.BlobKey, art.ExpiresAt); e != nil {
					return e
				}
			}
			body, meta, e := a.Fetch(ctx, ref(j), art.Index)
			if e != nil {
				return e
			}
			declared := meta.ContentType
			if declared == "" || declared == "application/octet-stream" {
				declared = "video/mp4"
			}
			sink := func(c context.Context, key string, r io.Reader) error {
				_, e := s.blobs.Put(c, key, r, artifacts.Meta{ContentType: declared, Bytes: meta.Bytes, ExpiresAt: art.ExpiresAt})
				return e
			}
			verifier := media.NewFetcher(sink)
			verified, e := verifier.Verify(ctx, body, declared, art.BlobKey, media.Limits{MaxBytes: s.cfg.Artifacts.MaxBytes, Deadline: s.cfg.Copy.Deadline})
			body.Close()
			if e != nil {
				return e
			}
			if verified.ContentType != "video/mp4" && verified.ContentType != "video/quicktime" {
				_ = s.blobs.Delete(ctx, art.BlobKey)
				return errors.New("invalid output media")
			}
			art.ContentType = verified.ContentType
			art.Bytes = verified.Bytes
			art.Width = verified.Width
			art.Height = verified.Height
			art.DurationSeconds = verified.DurationSeconds
			art.State = video.ArtifactAvailable
			if s.sweeper != nil {
				if e = s.sweeper.Track(ctx, art.BlobKey, art.ExpiresAt); e != nil {
					return e
				}
			}
			if e = s.save(ctx, j, l); e != nil {
				return e
			}
			if e = s.hit("copy.saved"); e != nil {
				return e
			}
		}
		return nil
	})
	if errors.Is(err, ErrCrash) || errors.Is(err, video.ErrLeaseLost) || errors.Is(err, video.ErrStoreUnavailable) {
		return err
	}
	if errors.Is(err, errLimited) {
		return s.saveLater(ctx, j, l, time.Second)
	}
	if err != nil {
		if j.CopyAttempts >= 3 || !s.clock.Now().Before(j.CopyBy) {
			return s.finishCopy(ctx, j, l, false)
		}
		// A refresh only polls the existing job; it can never regenerate output.
		if a.Capabilities().OutputURLRefresh {
			if e := s.refreshOutputs(ctx, j, a); e != nil {
				return s.saveLater(ctx, j, l, s.baseInterval(j))
			}
		}
		return s.saveLater(ctx, j, l, time.Duration(j.CopyAttempts)*time.Second)
	}
	return s.finishCopy(ctx, j, l, true)
}
func (s *Service) refreshOutputs(ctx context.Context, j *video.VideoJob, a provider.Adapter) error {
	ok, _, e := s.rate(ctx, j, "poll")
	if e != nil {
		return e
	}
	if !ok {
		return errLimited
	}
	return s.withPermit(ctx, j, "poll", func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, s.cfg.Providers[j.Service].Deadlines.Read)
		defer cancel()
		o, e := a.Poll(ctx, ref(j))
		if e == nil && o.Normalized == provider.StateCompleted && j.Observation != nil {
			j.Observation.Outputs = o.Outputs
		}
		return e
	})
}
func (s *Service) finishCopy(ctx context.Context, j *video.VideoJob, l video.Lease, success bool) error {
	for i := range j.Artifacts {
		a := &j.Artifacts[i]
		if a.State != video.ArtifactAvailable {
			a.State = video.ArtifactUnavailable
			a.Error = &video.VideoError{Code: "output_unavailable", Message: "generated output could not be stored"}
		}
	}
	now := s.clock.Now()
	j.Status = video.StatusCompleted
	j.CompletedAt = &now
	j.UpstreamMayContinue = false
	j.ReconcileState = video.ReconcileResolved
	if e := s.save(ctx, j, l); e != nil {
		return e
	}
	if e := s.hit("copy.terminal"); e != nil {
		return e
	}
	return s.settle(ctx, j, l)
}
func (s *Service) expireArtifact(ctx context.Context, key string) error {
	// All lifecycle blob keys end in /<video_id>/<input-or-output>. Keys carry no
	// tenant identity; the owning job is loaded under a fence before mutation.
	id := path.Base(path.Dir(key))
	if !strings.HasPrefix(id, "video_") {
		return artifacts.ErrKey
	}
	l, e := s.store.Lease(ctx, id, video.NewID(), s.cfg.Submit.LeaseTTL)
	if errors.Is(e, video.ErrVideoJobNotFound) {
		return nil
	}
	if e != nil {
		return e
	}
	defer s.store.Release(ctx, l)
	j, e := s.store.GetAccounting(ctx, id)
	if e != nil {
		return e
	}
	for i := range j.Artifacts {
		if j.Artifacts[i].BlobKey == key && j.Artifacts[i].State != video.ArtifactDeleted {
			j.Artifacts[i].State = video.ArtifactExpired
		}
	}
	return s.save(ctx, j, l)
}
