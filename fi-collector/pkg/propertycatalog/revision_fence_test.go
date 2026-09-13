package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const pythonRevisionFenceV2Fixture = `{"format":"futureagi.property-catalog-revision-fence","version":2,"fences":[{"organization_id":"11111111-1111-4111-8111-111111111111","workspace_id":"22222222-2222-4222-8222-222222222222","catalog_epoch":1,"catalog_revision":2,"projection_version":1,"build_lease_sha256":"74b3c71a4e280b332debb5c25b7f8e50d7d1513ecf4c08f624a0a7be7f0da0c6","build_token":"55555555-5555-4555-8555-555555555555","project_ids":["33333333-3333-4333-8333-333333333333","77777777-7777-4777-8777-777777777777"],"span_since_us":1786708800000000,"span_until_us":1786712400000000,"issued_at":"2026-08-14 12:00:00.000000","expires_at":"2026-08-14 12:10:00.000000","drain_deadline":"","fenced_sequence":0,"status":"building","fence_sha256":"9883a4f8a4f4032931ab9e4b31d73ec41e3e9f8587ea2c06c4d4db35c6174d0e"}]}
`

func testRevisionFence(revision uint64, status string) RevisionFence {
	spanSinceUS, spanUntilUS := testSpanWindow()
	fence := RevisionFence{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: revision, ProjectionVersion: 1,
		BuildLeaseSHA256: testDigest("build-lease"),
		BuildToken:       "55555555-5555-4555-8555-555555555555",
		ProjectIDs:       []string{testProject, testProjectTwo},
		SpanSinceUS:      spanSinceUS,
		SpanUntilUS:      spanUntilUS,
		IssuedAt:         "2026-08-14 11:59:00.000000",
		ExpiresAt:        "2026-08-14 12:02:00.000000",
		Status:           status,
	}
	fence.FenceSHA256 = RevisionFenceSHA256(fence)
	return fence
}

func TestFileRevisionProviderReadsOnlyCanonicalUnexpiredBuildingFence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fence.json")
	raw, err := EncodeRevisionFenceFile([]RevisionFence{testRevisionFence(17, "building")})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"build_lease_sha256"`) ||
		!strings.Contains(string(raw), `"project_ids"`) ||
		!strings.Contains(string(raw), `"span_since_us"`) ||
		strings.Contains(string(raw), `"source_manifest_sha256"`) {
		t.Fatalf("revision assignment uses stale lease field: %s", raw)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, err := NewFileRevisionProvider(path)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:00:00.000000")
		return value
	}
	fence, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
	if err != nil || fence.CatalogRevision != 17 || fence.FenceSHA256 != RevisionFenceSHA256(fence) {
		t.Fatalf("fence=%+v err=%v", fence, err)
	}

	fenced := testRevisionFence(17, "fenced")
	raw, _ = EncodeRevisionFenceFile([]RevisionFence{fenced})
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	gotFenced, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
	if err != nil || gotFenced.Status != "fenced" {
		t.Fatalf("fenced assignment=%+v err=%v", gotFenced, err)
	}
}

func TestRevisionFenceV2MatchesCanonicalPythonAssignmentBytes(t *testing.T) {
	fence := RevisionFence{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 1, CatalogRevision: 2, ProjectionVersion: 1,
		BuildLeaseSHA256: "74b3c71a4e280b332debb5c25b7f8e50d7d1513ecf4c08f624a0a7be7f0da0c6",
		BuildToken:       testBuildToken,
		ProjectIDs:       []string{testProject, "77777777-7777-4777-8777-777777777777"},
		SpanSinceUS:      1786708800000000, SpanUntilUS: 1786712400000000,
		IssuedAt: "2026-08-14 12:00:00.000000", ExpiresAt: "2026-08-14 12:10:00.000000",
		Status: "building",
	}
	raw, err := EncodeRevisionFenceFile([]RevisionFence{fence})
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(raw, []byte(pythonRevisionFenceV2Fixture)) {
		t.Fatalf("cross-language fence bytes drifted:\nGo:     %s\nPython: %s", raw, pythonRevisionFenceV2Fixture)
	}
	path := filepath.Join(t.TempDir(), "fence.json")
	if err := os.WriteFile(path, []byte(pythonRevisionFenceV2Fixture), 0o600); err != nil {
		t.Fatal(err)
	}
	provider, err := NewFileRevisionProvider(path)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:01:00.000000")
		return value
	}
	got, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
	if err != nil || got.FenceSHA256 != "9883a4f8a4f4032931ab9e4b31d73ec41e3e9f8587ea2c06c4d4db35c6174d0e" ||
		got.FenceSHA256 != RevisionFenceSHA256(got) {
		t.Fatalf("Python v2 assignment fence=%+v err=%v", got, err)
	}
}

func TestRevisionFenceHasNoFixedWorkspaceCountCap(t *testing.T) {
	fences := make([]RevisionFence, 300)
	for index := range fences {
		fence := testRevisionFence(uint64(index+1), "building")
		fence.WorkspaceID = fmt.Sprintf("00000000-0000-4000-8000-%012x", index+1)
		fences[index] = fence
	}
	raw, err := EncodeRevisionFenceFile(fences)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "fence.json")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, err := NewFileRevisionProvider(path)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:00:00.000000")
		return value
	}
	got, err := provider.CurrentRevisions(context.Background())
	if err != nil || len(got) != len(fences) {
		t.Fatalf("fences=%d err=%v", len(got), err)
	}
}

func TestFileRevisionProviderValidatesDrainingBoundaryAndDeadline(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fence.json")
	draining := testRevisionFence(17, "draining")
	draining.DrainDeadline = "2026-08-14 12:01:30.000000"
	draining.FencedSequence = 3
	raw, err := EncodeRevisionFenceFile([]RevisionFence{draining})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, _ := NewFileRevisionProvider(path)
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:00:00.000000")
		return value
	}
	fence, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
	if err != nil || fence.Status != "draining" || fence.FencedSequence != 3 {
		t.Fatalf("draining fence=%+v err=%v", fence, err)
	}

	draining.DrainDeadline = "2026-08-14 11:59:30.000000"
	raw, _ = EncodeRevisionFenceFile([]RevisionFence{draining})
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); !errors.Is(err, ErrRevisionNotAssigned) {
		t.Fatalf("expired drain error=%v", err)
	}
}

func TestFileRevisionProviderAcceptsExtendedInitialBuildLease(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fence.json")
	draining := testRevisionFence(17, "draining")
	draining.IssuedAt = "2026-08-14 11:00:00.000000"
	draining.ExpiresAt = "2026-08-14 12:00:00.000000"
	draining.DrainDeadline = draining.ExpiresAt
	draining.FencedSequence = 3
	raw, err := EncodeRevisionFenceFile([]RevisionFence{draining})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, _ := NewFileRevisionProvider(path)
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 11:59:00.000000")
		return value
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err != nil {
		t.Fatalf("60-minute Python lease was rejected: %v", err)
	}

	draining.ExpiresAt = "2026-08-14 12:00:00.000001"
	draining.DrainDeadline = draining.ExpiresAt
	raw, _ = EncodeRevisionFenceFile([]RevisionFence{draining})
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err == nil ||
		!strings.Contains(err.Error(), "too wide") {
		t.Fatalf("overwide drain error=%v", err)
	}
}

func TestFileRevisionProviderRejectsExpiredTamperedAndWritableFence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fence.json")
	raw, _ := EncodeRevisionFenceFile([]RevisionFence{testRevisionFence(17, "building")})
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	provider, _ := NewFileRevisionProvider(path)
	provider.now = func() time.Time {
		value, _ := time.Parse(dateTime64Layout, "2026-08-14 12:03:00.000000")
		return value
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); !errors.Is(err, ErrRevisionNotAssigned) {
		t.Fatalf("expired error=%v", err)
	}

	provider.now = time.Now
	tampered := strings.Replace(string(raw), `"catalog_revision":17`, `"catalog_revision":18`, 1)
	if err := os.WriteFile(path, []byte(tampered), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err == nil {
		t.Fatal("tampered fence was accepted")
	}
	if err := os.WriteFile(path, raw, 0o666); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0o666); err != nil {
		t.Fatal(err)
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err == nil ||
		!strings.Contains(err.Error(), "writable") {
		t.Fatalf("permissions error=%v", err)
	}
}

func TestExpiredAssignmentDoesNotBlockAnotherWorkspace(t *testing.T) {
	for _, status := range []string{"building", "draining"} {
		t.Run(status, func(t *testing.T) {
			expired := testRevisionFence(17, status)
			if status == "draining" {
				expired.DrainDeadline = expired.ExpiresAt
				expired.FencedSequence = 1
			}
			active := testRevisionFence(18, "building")
			active.WorkspaceID = "88888888-8888-4888-8888-888888888888"
			active.ExpiresAt = "2026-08-14 12:04:00.000000"
			raw, err := EncodeRevisionFenceFile([]RevisionFence{expired, active})
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), "fence.json")
			if err := os.WriteFile(path, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			provider, err := NewFileRevisionProvider(path)
			if err != nil {
				t.Fatal(err)
			}
			provider.now = func() time.Time {
				value, _ := time.Parse(dateTime64Layout, expired.ExpiresAt)
				return value
			}
			fences, err := provider.CurrentRevisions(context.Background())
			if err != nil || len(fences) != 1 || fences[0].WorkspaceID != active.WorkspaceID {
				t.Fatalf("expired tenant blocked current tenant: fences=%+v err=%v", fences, err)
			}
			if _, err := provider.CurrentRevision(context.Background(), expired.OrganizationID, expired.WorkspaceID); !errors.Is(err, ErrRevisionNotAssigned) {
				t.Fatalf("expired assignment admitted: %v", err)
			}
			retained, err := provider.retainedRevisionFences(context.Background())
			if err != nil || len(retained) != 2 || retained[0].WorkspaceID != expired.WorkspaceID {
				t.Fatalf("expired safety evidence lost: %+v %v", retained, err)
			}
			if err := validateRevisionFence(retained[0], provider.now()); !errors.Is(err, errRevisionFenceExpired) {
				t.Fatalf("retention changed direct expiry validation: %v", err)
			}
			if fence, err := provider.CurrentRevision(context.Background(), active.OrganizationID, active.WorkspaceID); err != nil || fence.CatalogRevision != active.CatalogRevision {
				t.Fatalf("active workspace lost its assignment: %+v %v", fence, err)
			}
			// Even when every assignment expires, the process can remain alive
			// without granting any tenant authority or inventing a new lease.
			provider.now = func() time.Time {
				value, _ := time.Parse(dateTime64Layout, active.ExpiresAt)
				return value
			}
			fences, err = provider.CurrentRevisions(context.Background())
			if err != nil || len(fences) != 0 {
				t.Fatalf("all expired: %+v %v", fences, err)
			}
			if retained, err := provider.retainedRevisionFences(context.Background()); err != nil || len(retained) != 2 {
				t.Fatalf("all-expired safety evidence lost: %+v %v", retained, err)
			}
			tampered := bytes.Replace(raw, []byte(`"catalog_revision":17`), []byte(`"catalog_revision":19`), 1)
			if err := os.WriteFile(path, tampered, 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := provider.CurrentRevisions(context.Background()); err == nil || !strings.Contains(err.Error(), "digest") {
				t.Fatalf("expired assignment hid tampering: %v", err)
			}
			if _, err := provider.retainedRevisionFences(context.Background()); err == nil || !strings.Contains(err.Error(), "digest") {
				t.Fatalf("retained expired assignment hid tampering: %v", err)
			}
		})
	}
}

func TestRevisionFenceReadersRejectMalformedExpiredInventory(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func([]RevisionFence) []RevisionFence
	}{
		{"source project", func(f []RevisionFence) []RevisionFence { f[0].ProjectIDs = []string{"invalid"}; return f }},
		{"source window", func(f []RevisionFence) []RevisionFence { f[0].SpanUntilUS = f[0].SpanSinceUS; return f }},
		{"issued timestamp", func(f []RevisionFence) []RevisionFence { f[0].IssuedAt = "invalid"; return f }},
		{"expiry timestamp", func(f []RevisionFence) []RevisionFence { f[0].ExpiresAt = "invalid"; return f }},
		{"unordered timestamps", func(f []RevisionFence) []RevisionFence { f[0].ExpiresAt = f[0].IssuedAt; return f }},
		{"building drain boundary", func(f []RevisionFence) []RevisionFence { f[0].FencedSequence = 1; return f }},
		{"invalid drain timestamp", func(f []RevisionFence) []RevisionFence {
			f[0].Status, f[0].DrainDeadline = "draining", "invalid"
			return f
		}},
		{"overwide expired drain", func(f []RevisionFence) []RevisionFence {
			f[0].Status, f[0].DrainDeadline = "draining", f[0].ExpiresAt
			f[0].IssuedAt = "2026-08-14 10:00:00.000000"
			return f
		}},
		{"duplicate expired scope", func(f []RevisionFence) []RevisionFence { return append(f, f[0]) }},
		{"duplicate expired and active scope", func(f []RevisionFence) []RevisionFence { f[1].WorkspaceID = f[0].WorkspaceID; return f }},
	} {
		t.Run(test.name, func(t *testing.T) {
			expired, active := testRevisionFence(17, "building"), testRevisionFence(18, "building")
			active.WorkspaceID = testWorkspaceTwo
			active.ExpiresAt = "2026-08-14 12:04:00.000000"
			// Recompute the digest so structural errors cannot be hidden by a
			// checksum failure. Only the first workspace is expired.
			raw, err := EncodeRevisionFenceFile(test.mutate([]RevisionFence{expired, active}))
			if err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), "fence.json")
			if err := os.WriteFile(path, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			provider, err := NewFileRevisionProvider(path)
			if err != nil {
				t.Fatal(err)
			}
			provider.now = func() time.Time {
				return time.Date(2026, 8, 14, 12, 3, 0, 0, time.UTC)
			}
			for _, read := range []func(context.Context) ([]RevisionFence, error){provider.CurrentRevisions, provider.retainedRevisionFences} {
				fences, err := read(context.Background())
				if err == nil || errors.Is(err, errRevisionFenceExpired) || errors.Is(err, ErrRevisionNotAssigned) || fences != nil {
					t.Fatalf("malformed expired inventory was not fatal: %+v %v", fences, err)
				}
			}
			if _, err := provider.CurrentRevision(context.Background(), active.OrganizationID, active.WorkspaceID); err == nil || errors.Is(err, ErrRevisionNotAssigned) {
				t.Fatalf("active lookup hid malformed expired inventory: %v", err)
			}
		})
	}
}
