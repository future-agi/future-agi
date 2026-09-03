package propertycatalog

import (
	"bytes"
	"context"
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
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err == nil ||
		!strings.Contains(err.Error(), "deadline") {
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
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err == nil ||
		!strings.Contains(err.Error(), "expired") {
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
