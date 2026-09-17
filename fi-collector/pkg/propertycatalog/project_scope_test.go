package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func projectInventoryFixture(count int) []string {
	projects := make([]string, count)
	for index := range projects {
		projects[index] = fmt.Sprintf("00000000-0000-4000-8000-%012x", index+1)
	}
	return projects
}

func buildPlanWithProjects(t *testing.T, raw string, projects []string) (string, string) {
	t.Helper()
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(raw), &plan); err != nil {
		t.Fatal(err)
	}
	plan.SourceScope.ProjectIDs = projects
	encoded, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded), testDigest(string(encoded))
}

func retirementWithProjects(t *testing.T, projects []string) ProducerStateRetirement {
	t.Helper()
	var document producerRetirementDocument
	if err := json.Unmarshal(pythonRetirementFixture(t), &document); err != nil {
		t.Fatal(err)
	}
	value := document.Retirements[0]
	value.BuildPlanJSON, value.BuildLeaseSHA256 = buildPlanWithProjects(t, value.BuildPlanJSON, projects)
	value.RetirementSHA256 = producerRetirementSHA256(value)
	return value
}

func projectFenceProvider(t *testing.T, projects []string) *FileRevisionProvider {
	t.Helper()
	fence := testRevisionFence(17, "building")
	fence.ProjectIDs = projects
	raw, err := EncodeRevisionFenceFile([]RevisionFence{fence})
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
		return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
	}
	return provider
}

func TestProjectInventoryCardinalityAndByteBoundaries(t *testing.T) {
	for _, count := range []int{178, 257, 1024} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			projects := projectInventoryFixture(count)
			t.Run("fence_round_trip", func(t *testing.T) {
				provider := projectFenceProvider(t, projects)
				before, err := os.ReadFile(provider.path)
				if err != nil {
					t.Fatal(err)
				}
				fence, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace)
				if err != nil || !reflect.DeepEqual(fence.ProjectIDs, projects) || fence.FenceSHA256 != RevisionFenceSHA256(fence) {
					t.Fatalf("project inventory did not round-trip: count=%d err=%v", len(fence.ProjectIDs), err)
				}
				after, err := EncodeRevisionFenceFile([]RevisionFence{fence})
				if err != nil || !bytes.Equal(before, after) {
					t.Fatalf("fence bytes changed across round-trip: %v", err)
				}
				if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspaceTwo); !errors.Is(err, ErrRevisionNotAssigned) {
					t.Fatalf("large project inventory granted another workspace authority: %v", err)
				}
			})
			t.Run("build_plan_and_checkpoint_inventory", func(t *testing.T) {
				template, _ := validBuildPlan()
				plan, digest := buildPlanWithProjects(t, template, projects)
				evidence, planErr := validateBuildPlan(plan, digest, validDeliveryLeaseRequest())
				rows := validCheckpointInventory(t, false)
				for index := range rows {
					rows[index].ReservationBuildPlanJSON, rows[index].StreamBuildPlanJSON = plan, plan
					rows[index].ReservationBuildLeaseSHA256, rows[index].StreamBuildLeaseSHA256 = digest, digest
				}
				streams, inventoryErr := validateCheckpointInventory(rows)
				// 1,024 canonical UUIDs alone exceed 32 KiB. Removing the
				// cardinality cap must not weaken the existing byte ceiling.
				if count == 1024 {
					if len(plan) <= maxBuildPlanBytes || planErr == nil || inventoryErr == nil ||
						!strings.Contains(planErr.Error(), "build plan bytes") || !strings.Contains(inventoryErr.Error(), "build plan bytes") {
						t.Fatalf("oversized plan accepted: bytes=%d plan=%v inventory=%v", len(plan), planErr, inventoryErr)
					}
					return
				}
				if planErr != nil || inventoryErr != nil || len(streams) != 10 || !reflect.DeepEqual(evidence.ProjectIDs, projects) {
					t.Fatalf("valid large build plan rejected: bytes=%d streams=%d plan=%v inventory=%v", len(plan), len(streams), planErr, inventoryErr)
				}
				request := validDeliveryLeaseRequest()
				request.WorkspaceID = testWorkspaceTwo
				if _, err := validateBuildPlan(plan, digest, request); err == nil {
					t.Fatal("large build plan authorized a different tenant")
				}
			})
			t.Run("retirement_round_trip", func(t *testing.T) {
				value := retirementWithProjects(t, projects)
				raw, err := json.Marshal(producerRetirementDocument{
					Format: producerRetirementFormat, Version: producerRetirementVersion,
					Retirements: []ProducerStateRetirement{value},
				})
				if err != nil {
					t.Fatal(err)
				}
				raw = append(raw, '\n')
				directory := t.TempDir()
				path := filepath.Join(directory, producerRetirementFileName)
				if err := os.WriteFile(path, raw, 0o600); err != nil {
					t.Fatal(err)
				}
				loaded, loadErr := loadProducerRetirements(directory)
				if count == 1024 {
					if len(value.BuildPlanJSON) <= maxBuildPlanBytes || loadErr == nil || !strings.Contains(loadErr.Error(), "build plan bytes") {
						t.Fatalf("retirement accepted oversized embedded plan: %v", loadErr)
					}
					return
				}
				if loadErr != nil || loaded[producerRetirementTenant{value.OrganizationID, value.WorkspaceID}] != value {
					t.Fatalf("retirement did not round-trip exactly: %v", loadErr)
				}
				after, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(raw, after) {
					t.Fatalf("retirement read changed proof bytes: %v", err)
				}
			})
		})
	}
}

func TestProjectInventoryStillRejectsMalformedScopes(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func([]string) []string
	}{
		{"empty", func(_ []string) []string { return []string{} }},
		{"duplicate", func(ids []string) []string { ids[1] = ids[0]; return ids }},
		{"unsorted", func(ids []string) []string { ids[0], ids[1] = ids[1], ids[0]; return ids }},
		{"noncanonical", func(ids []string) []string { ids[len(ids)-1] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"; return ids }},
		{"malformed", func(ids []string) []string { ids[len(ids)-1] = "not-a-uuid"; return ids }},
	} {
		t.Run(test.name, func(t *testing.T) {
			projects := test.mutate(projectInventoryFixture(257))
			provider := projectFenceProvider(t, projects)
			if _, err := provider.CurrentRevisions(context.Background()); err == nil {
				t.Fatal("fence accepted malformed project inventory")
			}
			template, _ := validBuildPlan()
			plan, digest := buildPlanWithProjects(t, template, projects)
			if len(plan) > maxBuildPlanBytes {
				t.Fatal("malformed-scope test must not fail on the byte limit")
			}
			if _, err := validateBuildPlan(plan, digest, validDeliveryLeaseRequest()); err == nil {
				t.Fatal("build plan accepted malformed project inventory")
			}
			if err := validateProducerRetirement(retirementWithProjects(t, projects)); err == nil {
				t.Fatal("retirement accepted malformed project inventory")
			}
		})
	}
}

func TestProjectInventoryLargeDeliveryPreservesIsolation(t *testing.T) {
	// Exercise delivery's validated-scope consumer independently of the real
	// lease reader's serialized build-plan byte limit, tested above.
	for _, count := range []int{178, 257, 1024} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			projects := projectInventoryFixture(count)
			for _, project := range []string{projects[0], projects[len(projects)-1], testProject} {
				t.Run(project, func(t *testing.T) {
					row := testValue()
					row.ProjectID = project
					sink := &recordingSink{}
					handler, err := NewDeliveryHandler(sink, &recordingLeaseGuard{role: "hot_values", projectIDs: projects}, time.Second)
					if err != nil {
						t.Fatal(err)
					}
					err = handler.Deliver(context.Background(), Delivery{
						Envelope:  mustEnvelope(t, hotValueDeliveryInput(t, row)),
						Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 1,
					})
					if project == testProject {
						if err == nil || !strings.Contains(err.Error(), "project is outside") || len(sink.calls) != 0 {
							t.Fatalf("foreign project reached sink: calls=%v err=%v", sink.calls, err)
						}
					} else if err != nil || fmt.Sprint(sink.calls) != "[data:span_attribute_value_catalog ledger]" {
						t.Fatalf("in-scope delivery rejected: calls=%v err=%v", sink.calls, err)
					}
				})
			}
		})
	}
}

func TestProjectInventoryLargeHotAdmissionRemainsAtomic(t *testing.T) {
	for _, count := range []int{178, 257, 1024} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			projects := projectInventoryFixture(count)
			provider := testMutableProvider(17, testWorkspace)
			fence := testRevisionFence(17, "building")
			fence.ProjectIDs = projects
			provider.set(fence)
			runtime, err := NewHotRuntime(validRuntimeConfig(t).WithDefaults(), provider, &recordingEnvelopePublisher{})
			if err != nil {
				t.Fatal(err)
			}
			inside := scopedHotRow(testWorkspace, projects[len(projects)-1],
				hotRow(projects[len(projects)-1], testSeen, map[string]string{"key": "inside"}, nil))
			outside := scopedHotRow(testWorkspace, testProject,
				hotRow(testProject, testSeen, map[string]string{"key": "outside"}, nil))
			if submission, err := runtime.admitSubmission([]ScopedSpan{inside, outside}); err == nil ||
				!strings.Contains(err.Error(), "project is outside") || len(submission.streams) != 0 || len(runtime.pendingAdmissions) != 0 {
				t.Fatalf("mixed project scope was not rejected atomically: pending=%d err=%v", len(runtime.pendingAdmissions), err)
			}
			outside = inside
			outside.Row = hotRow(projects[len(projects)-1],
				time.UnixMicro(int64(fence.SpanUntilUS)).UTC().Format(dateTime64Layout),
				map[string]string{"key": "exclusive upper"}, nil)
			if submission, err := runtime.admitSubmission([]ScopedSpan{inside, outside}); err == nil ||
				!strings.Contains(err.Error(), "half-open") || len(submission.streams) != 0 || len(runtime.pendingAdmissions) != 0 {
				t.Fatalf("out-of-window scope was not rejected atomically: pending=%d err=%v", len(runtime.pendingAdmissions), err)
			}
			if submission, err := runtime.admitSubmission([]ScopedSpan{inside}); err != nil || len(submission.streams) != 1 {
				t.Fatalf("in-scope hot admission rejected: streams=%d err=%v", len(submission.streams), err)
			}
		})
	}
}

func TestProjectInventoryRetainsControlFileByteLimits(t *testing.T) {
	for _, test := range []struct {
		name  string
		limit int64
	}{
		{"fence.json", maxRevisionFenceBytes},
		{producerRetirementFileName, maxProducerRetirementBytes},
	} {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			path := filepath.Join(directory, test.name)
			file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
			if err != nil {
				t.Fatal(err)
			}
			defer file.Close()
			// Sparse length tests the pre-read size gate without allocating 64 MiB.
			if err := file.Truncate(test.limit + 1); err != nil {
				t.Fatal(err)
			}
			if test.name == producerRetirementFileName {
				if _, err := loadProducerRetirements(directory); err == nil || !strings.Contains(err.Error(), "size is unsafe") {
					t.Fatalf("retirement byte bound lost: %v", err)
				}
			} else {
				provider, err := NewFileRevisionProvider(path)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := provider.CurrentRevisions(context.Background()); err == nil || !strings.Contains(err.Error(), "bounded regular file") {
					t.Fatalf("fence byte bound lost: %v", err)
				}
			}
		})
	}
}
