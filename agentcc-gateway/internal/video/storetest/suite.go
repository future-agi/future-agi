// Package storetest defines the shared behavioral contract for memory and Redis.
package storetest

import (
	"context"
	"errors"
	video "github.com/futureagi/agentcc-gateway/internal/video"
	"testing"
	"time"
)

type Factory func(*testing.T) video.Store

func Job(id, org string) *video.VideoJob {
	return &video.VideoJob{ID: id, OrgID: org, Status: video.StatusSubmitting, Phase: video.PhasePrepared, Model: "byteplus/model", Service: "byteplus", ModelID: "model", Region: "ap-southeast-1", AccountRef: "account", Fingerprint: "fingerprint", CreatedAt: time.Now().UTC().Truncate(time.Second), ExpiresAt: time.Now().Add(time.Hour).UTC().Truncate(time.Second), SubmitBy: time.Now().Add(time.Minute).UTC().Truncate(time.Second)}
}
func Run(t *testing.T, newStore Factory) {
	t.Run("CRUDAndIsolation", func(t *testing.T) {
		s := newStore(t)
		j := Job("video_a", "a")
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		if err := s.Create(j); !errors.Is(err, video.ErrVideoJobExists) {
			t.Fatal(err)
		}
		got, err := s.GetForOrg(j.ID, "a")
		if err != nil {
			t.Fatal(err)
		}
		got.ClientMetadata = map[string]string{"mutation": "x"}
		again, _ := s.Get(j.ID)
		if len(again.ClientMetadata) != 0 {
			t.Fatal("store aliases caller")
		}
		for _, org := range []string{"b", ""} {
			if _, err := s.GetForOrg(j.ID, org); !errors.Is(err, video.ErrVideoJobNotFound) {
				t.Fatal(err)
			}
		}
		j.Status = video.StatusFailed
		j.Error = &video.VideoError{Code: "validation"}
		if err := s.Update(j); err != nil {
			t.Fatal(err)
		}
		j.Status = video.StatusRunning
		if err := s.Update(j); !errors.Is(err, video.ErrIllegalTransition) {
			t.Fatal(err)
		}
	})
	t.Run("OwnerlessQuarantined", func(t *testing.T) {
		s := newStore(t)
		j := Job("video_ownerless", "")
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		if _, err := s.Get(j.ID); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
		if _, err := s.GetAccounting(context.Background(), j.ID); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
		rows, _, err := s.ListByOrg("", video.VideoListFilters{Limit: 100})
		if err != nil || len(rows) != 0 {
			t.Fatal(rows, err)
		}
		active, err := s.GetActiveJobs()
		if err != nil || len(active) != 0 {
			t.Fatal(active, err)
		}
		claimed, err := s.ClaimSubmit(context.Background(), "worker", time.Second, 10)
		if err != nil || len(claimed) != 0 {
			t.Fatal(claimed, err)
		}
	})
	t.Run("ListOrderingFiltersPagination", func(t *testing.T) {
		s := newStore(t)
		now := time.Now().UTC().Truncate(time.Second)
		for _, id := range []string{"video_a", "video_c", "video_b"} {
			j := Job(id, "org")
			j.CreatedAt = now
			if err := s.Create(j); err != nil {
				t.Fatal(err)
			}
		}
		other := Job("video_foreign", "other")
		if err := s.Create(other); err != nil {
			t.Fatal(err)
		}
		for _, tc := range []struct {
			order  string
			offset int
			want   string
		}{{"desc", 0, "video_c"}, {"asc", 1, "video_b"}, {"desc", 2, "video_a"}} {
			rows, total, err := s.ListByOrg("org", video.VideoListFilters{Limit: 1, Offset: tc.offset, Order: tc.order})
			if err != nil || total != 3 || len(rows) != 1 || rows[0].ID != tc.want {
				t.Fatal(rows, total, err)
			}
		}
		rows, total, err := s.ListByOrg("org", video.VideoListFilters{Limit: 10, Status: video.StatusCompleted})
		if err != nil || len(rows) != 0 || total != 0 {
			t.Fatal(rows, total, err)
		}
		if _, _, err := s.ListByOrg("org", video.VideoListFilters{Limit: 1, Offset: -1}); err == nil {
			t.Fatal("negative offset")
		}
	})
	t.Run("AcceptReplayConflictAndTombstone", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_accept", "org")
		req := video.AcceptRequest{Job: j, Operation: "submit", IdempotencyKey: "same"}
		first, err := s.Accept(ctx, req)
		if err != nil || first.Replay {
			t.Fatal(first, err)
		}
		req.Job = Job("video_replay", "org")
		again, err := s.Accept(ctx, req)
		if err != nil || !again.Replay || again.Job.ID != j.ID {
			t.Fatal(again, err)
		}
		req.Job.Fingerprint = "changed"
		if _, err := s.Accept(ctx, req); !errors.Is(err, video.ErrIdempotencyConflict) {
			t.Fatal(err)
		}
		if err := s.Delete(j.ID); err != nil {
			t.Fatal(err)
		}
		if err := s.Delete(j.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := s.Get(j.ID); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
		req.Job.Fingerprint = j.Fingerprint
		result, err := s.Accept(ctx, req)
		if !errors.Is(err, video.ErrDeletedJob) || result.Job.ID != j.ID {
			t.Fatal(result, err)
		}
		tomb, err := s.GetAccounting(ctx, j.ID)
		if err != nil || tomb.DeletedAt == nil || tomb.Status != video.StatusSubmitting || tomb.RequestCanonical != nil || tomb.Prompt != "" {
			t.Fatal(tomb, err)
		}
	})
	t.Run("LeasesFenceAndTakeover", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_lease", "org")
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		lease, err := s.Lease(ctx, j.ID, "a", 30*time.Millisecond)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := s.Lease(ctx, j.ID, "b", time.Second); !errors.Is(err, video.ErrLeaseHeld) {
			t.Fatal(err)
		}
		time.Sleep(50 * time.Millisecond)
		fresh, err := s.Lease(ctx, j.ID, "b", time.Second)
		if err != nil || fresh.Fence <= lease.Fence {
			t.Fatal(fresh, err)
		}
		j.Phase = video.PhaseCalling
		if err := s.Save(ctx, j, lease); !errors.Is(err, video.ErrLeaseLost) {
			t.Fatal(err)
		}
		if err := s.Renew(ctx, lease, time.Second); !errors.Is(err, video.ErrLeaseLost) {
			t.Fatal(err)
		}
		if err := s.Release(ctx, lease); !errors.Is(err, video.ErrLeaseLost) {
			t.Fatal(err)
		}
		if err := s.Save(ctx, j, fresh); err != nil {
			t.Fatal(err)
		}
		if err := s.Renew(ctx, fresh, time.Second); err != nil {
			t.Fatal(err)
		}
		if err := s.Release(ctx, fresh); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("QueueClaimsAndTransitions", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_queue", "org")
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		claims, err := s.ClaimSubmit(ctx, "worker", time.Second, 1)
		if err != nil || len(claims) != 1 {
			t.Fatal(claims, err)
		}
		job := claims[0].Job
		lease := claims[0].Lease
		job.Phase = video.PhaseCalling
		if err := s.Save(ctx, job, lease); err != nil {
			t.Fatal(err)
		}
		job.Status = video.StatusQueued
		job.Phase = video.PhaseReceived
		job.ProviderJobID = "provider"
		job.NextPollAt = time.Now().Add(-time.Second)
		if err := s.Save(ctx, job, lease); err != nil {
			t.Fatal(err)
		}
		if err := s.Release(ctx, lease); err != nil {
			t.Fatal(err)
		}
		claims, err = s.ClaimSubmit(ctx, "worker", time.Second, 1)
		if err != nil || len(claims) != 0 {
			t.Fatal(claims, err)
		}
		claims, err = s.ClaimDue(ctx, "worker", time.Second, 1)
		if err != nil || len(claims) != 1 {
			t.Fatal(claims, err)
		}
		job = claims[0].Job
		lease = claims[0].Lease
		job.Status = video.StatusFailed
		job.ReconcileState = video.ReconcilePending
		job.NextPollAt = time.Now().Add(-time.Second)
		if err := s.Save(ctx, job, lease); err != nil {
			t.Fatal(err)
		}
		if err := s.Release(ctx, lease); err != nil {
			t.Fatal(err)
		}
		claims, err = s.ClaimReconcile(ctx, "worker", time.Second, 1)
		if err != nil || len(claims) != 1 {
			t.Fatal(claims, err)
		}
	})
	t.Run("GCProtectsAccounting", func(t *testing.T) {
		s := newStore(t)
		for _, tc := range []struct {
			id, settlement, reconcile string
			keep                      bool
		}{{"video_settled", video.SettlementSettled, "", false}, {"video_reserved", video.SettlementReserved, "", true}, {"video_unsettled", video.SettlementUnsettled, "", true}, {"video_pending", video.SettlementSettled, video.ReconcilePending, true}} {
			j := Job(tc.id, "org")
			j.Status = video.StatusFailed
			j.SettlementState = tc.settlement
			j.ReconcileState = tc.reconcile
			j.ExpiresAt = time.Now().Add(-time.Hour)
			if err := s.Create(j); err != nil {
				t.Fatal(err)
			}
		}
		n, err := s.GarbageCollect()
		if err != nil || n != 1 {
			t.Fatal(n, err)
		}
		if _, err := s.Get("video_settled"); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
		for _, id := range []string{"video_reserved", "video_unsettled", "video_pending"} {
			if _, err := s.Get(id); err != nil {
				t.Fatal(id, err)
			}
		}
	})
	t.Run("DeleteCannotResurrect", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_deleted", "org")
		j.Prompt = "private"
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		if err := s.Delete(j.ID); err != nil {
			t.Fatal(err)
		}
		lease, err := s.Lease(ctx, j.ID, "settler", time.Second)
		if err != nil {
			t.Fatal(err)
		}
		j.Phase = video.PhaseCalling
		j.Artifacts = []video.Artifact{{State: video.ArtifactAvailable, BlobKey: "private"}}
		if err := s.Save(ctx, j, lease); err != nil {
			t.Fatal(err)
		}
		got, err := s.GetAccounting(ctx, j.ID)
		if err != nil || got.DeletedAt == nil || got.Prompt != "" || len(got.Artifacts) != 0 {
			t.Fatal(got, err)
		}
	})
	t.Run("PinnedIdentityAndDeadlines", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_pinned", "org")
		j.KeyID = "key"
		j.TariffRevision = "price"
		j.RunBy = time.Now().Add(time.Hour).UTC().Truncate(time.Second)
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		l, err := s.Lease(ctx, j.ID, "worker", time.Second)
		if err != nil {
			t.Fatal(err)
		}
		for _, change := range []func(*video.VideoJob){func(j *video.VideoJob) { j.OrgID = "other" }, func(j *video.VideoJob) { j.KeyID = "other" }, func(j *video.VideoJob) { j.ModelID = "other" }, func(j *video.VideoJob) { j.TariffRevision = "other" }, func(j *video.VideoJob) { j.RunBy = j.RunBy.Add(time.Hour) }, func(j *video.VideoJob) { j.SubmitBy = j.SubmitBy.Add(time.Hour) }} {
			copy, err := s.Get(j.ID)
			if err != nil {
				t.Fatal(err)
			}
			change(copy)
			if err := s.Save(ctx, copy, l); err == nil {
				t.Fatal("mutated pin accepted")
			}
		}
	})
	t.Run("DueBackoffAndOrgIdempotency", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		for _, org := range []string{"a", "b"} {
			j := Job("video_scope"+org, org)
			j.NextPollAt = time.Now().Add(time.Hour)
			result, err := s.Accept(ctx, video.AcceptRequest{Job: j, IdempotencyKey: "same", Operation: "submit"})
			if err != nil || result.Replay {
				t.Fatal(result, err)
			}
		}
		claims, err := s.ClaimSubmit(ctx, "worker", time.Second, 10)
		if err != nil || len(claims) != 0 {
			t.Fatal(claims, err)
		}
	})
	t.Run("MissingAndLeaseProtectedDelete", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		if err := s.Delete("absent"); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
		j := Job("video_delete_fence", "org")
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		l, err := s.Lease(ctx, j.ID, "worker", time.Second)
		if err != nil {
			t.Fatal(err)
		}
		wrong := l
		wrong.Fence++
		if err := s.Tombstone(ctx, j.ID, "org", wrong); !errors.Is(err, video.ErrLeaseLost) {
			t.Fatal(err)
		}
		if err := s.Tombstone(ctx, j.ID, "other", l); !errors.Is(err, video.ErrVideoJobNotFound) {
			t.Fatal(err)
		}
	})

	t.Run("CompletedArtifactExpiry", func(t *testing.T) {
		s := newStore(t)
		ctx := context.Background()
		j := Job("video_complete", "org")
		j.Status = video.StatusQueued
		j.Phase = video.PhaseReceived
		j.ProviderJobID = "upstream"
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
		l, err := s.Lease(ctx, j.ID, "worker", time.Second)
		if err != nil {
			t.Fatal(err)
		}
		j.Status = video.StatusCompleted
		if err := s.Save(ctx, j, l); err == nil {
			t.Fatal("completed without artifact")
		}
		j.Artifacts = []video.Artifact{{State: video.ArtifactUnavailable}}
		if err := s.Save(ctx, j, l); err != nil {
			t.Fatal(err)
		}
		j.Artifacts[0].State = video.ArtifactExpired
		if err := s.Save(ctx, j, l); err != nil {
			t.Fatal("expired artifact regressed terminal state", err)
		}
		j.Status = video.StatusRunning
		if err := s.Save(ctx, j, l); !errors.Is(err, video.ErrIllegalTransition) {
			t.Fatal(err)
		}
	})

}
