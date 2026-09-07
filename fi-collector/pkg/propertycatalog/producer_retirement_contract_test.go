package propertycatalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

func retirementContractPlan(t *testing.T, value ProducerStateRetirement) buildPlanDocumentJSON {
	t.Helper()
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(value.BuildPlanJSON), &plan); err != nil {
		t.Fatal(err)
	}
	return plan
}

func sortRetirementContractStreams(plan *buildPlanDocumentJSON) {
	sort.Slice(plan.Streams, func(i, j int) bool {
		left, right := plan.Streams[i], plan.Streams[j]
		if left.SourceAdapter != right.SourceAdapter {
			return left.SourceAdapter < right.SourceAdapter
		}
		if left.Role != right.Role {
			return left.Role < right.Role
		}
		return left.ProducerStreamID < right.ProducerStreamID
	})
}

func TestProducerRetirementFormatContractPreservesLifecycleRecords(t *testing.T) {
	for _, mode := range []string{"initial_backfill", "full_repair", "incremental", "physical_snapshot"} {
		for _, coordinates := range []struct {
			epoch      uint16
			revision   uint64
			projection uint16
		}{
			{1, 3, 3},
			{3, 17, 1},
			{7, 41, 9},
			{65535, ^uint64(0), 65535},
		} {
			name := fmt.Sprintf("%s/epoch%d_revision%d_projection%d", mode,
				coordinates.epoch, coordinates.revision, coordinates.projection)
			t.Run(name, func(t *testing.T) {
				value := physicalSnapshotRetirementFixture(t)
				plan := retirementContractPlan(t, value)
				value.CatalogEpoch = coordinates.epoch
				value.CatalogRevision = coordinates.revision
				value.ProjectionVersion = coordinates.projection
				value.LineageAnchorRevision = value.CatalogRevision
				plan.CatalogEpoch = value.CatalogEpoch
				plan.CatalogRevision = value.CatalogRevision
				plan.ProjectionVersion = value.ProjectionVersion
				label := fmt.Sprintf("physical_snapshot_r%d", value.CatalogRevision)
				if mode != "physical_snapshot" {
					value.LifecycleMode = mode
					label = mode + "_source_cutoff"
				}
				if mode == "incremental" {
					value.LineageAnchorRevision--
					value.LineageAnchorBuildToken = testStream
					value.ActivationSequence++
					value.ActiveRevisionsSinceAnchor = 1
				}
				for index := range plan.Streams {
					plan.Streams[index].SourceCutoff.Label = label
					if mode != "physical_snapshot" {
						// Normal lifecycle streams may have independent cutoffs.
						plan.Streams[index].SourceCutoff.Value += uint64(index)
					}
				}
				setRetirementPlan(t, &value, plan)
				if err := validateProducerRetirement(value); err != nil {
					t.Fatalf("valid retirement rejected: %v", err)
				}

				// Loading compatibility evidence must preserve the exact persisted
				// record, including its labels, coordinates, and both digests.
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
				loaded, err := loadProducerRetirements(directory)
				if err != nil {
					t.Fatal(err)
				}
				if len(loaded) != 1 || loaded[producerRetirementTenant{value.OrganizationID, value.WorkspaceID}] != value {
					t.Fatal("loading changed the retirement record")
				}
				after, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(raw, after) {
					t.Fatalf("loading changed the persisted bytes: %v", err)
				}
			})
		}
	}
}

func TestProducerRetirementPhysicalSnapshotRequiresExactRoleInventory(t *testing.T) {
	base := physicalSnapshotRetirementFixture(t)
	for index, stream := range retirementContractPlan(t, base).Streams {
		name := string(stream.SourceAdapter) + "/" + stream.Role
		for _, mutation := range []string{"missing", "extra", "mismatched"} {
			t.Run(name+"/"+mutation, func(t *testing.T) {
				value := base
				plan := retirementContractPlan(t, value)
				switch mutation {
				case "missing":
					plan.Streams = append(plan.Streams[:index], plan.Streams[index+1:]...)
				case "extra":
					extra := stream
					extra.ProducerStreamID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
					plan.Streams = append(plan.Streams, extra)
				case "mismatched":
					// Keep the count and use a known role so validation must
					// enforce the adapter's role inventory, not merely syntax.
					plan.Streams[index].Role = "definitions"
					if stream.Role == "definitions" {
						plan.Streams[index].Role = "values"
					}
				}
				sortRetirementContractStreams(&plan)
				setRetirementPlan(t, &value, plan)
				if err := validateProducerRetirement(value); err == nil || !strings.Contains(err.Error(), "retirement build plan:") {
					t.Fatalf("invalid role inventory was not rejected by the build contract: %v", err)
				}
			})
		}
	}
}

func TestProducerRetirementPhysicalSnapshotKeepsProofChecks(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*ProducerStateRetirement, *buildPlanDocumentJSON)
		want   string
	}{
		{"unknown format", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Format += "-unknown"
		}, "exact revision lease"},
		{"unknown version", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Version++
		}, "exact revision lease"},
		{"empty streams", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams = nil
		}, "exact revision lease"},
		{"unknown adapter", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams[0].SourceAdapter = "unknown"
		}, "is invalid"},
		{"unknown role", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams[0].Role = "unknown"
		}, "is invalid"},
		{"missing adapter replaced by extra known adapter", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			// Preserve the stream count and valid roles, but duplicate another
			// definition adapter instead of covering the missing source.
			plan.Streams[0].SourceAdapter = plan.Streams[1].SourceAdapter
			sortRetirementContractStreams(plan)
		}, "lacks one definition stream"},
		{"duplicate source stream across roles", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			for index := range plan.Streams {
				if plan.Streams[index].SourceAdapter == AdapterSpanAttribute {
					plan.Streams[index].ProducerStreamID = pythonRetirementHotStream
				}
			}
		}, "duplicate source stream"},
		{"noncanonical stream order", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams[0], plan.Streams[1] = plan.Streams[1], plan.Streams[0]
		}, "not uniquely canonical-sorted"},
		{"malformed stream ID", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams[0].ProducerStreamID = "unknown"
		}, "is invalid"},
		{"incomplete source scope", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.SourceScope.ProjectIDs = nil
		}, "build plan source scope"},
		{"different epoch", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.CatalogEpoch++
		}, "exact revision lease"},
		{"different revision", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.CatalogRevision++
		}, "exact revision lease"},
		{"different projection", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.ProjectionVersion++
		}, "exact revision lease"},
		{"different build token", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.BuildToken = testStream
		}, "exact revision lease"},
		{"different tenant", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.WorkspaceID = testWorkspaceTwo
		}, "exact revision lease"},
		{"absent hot producer", func(value *ProducerStateRetirement, _ *buildPlanDocumentJSON) {
			value.HotProducerStreamID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
		}, "current source stream is absent"},
		{"wrong hot producer role", func(value *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			for _, stream := range plan.Streams {
				if stream.SourceAdapter == AdapterSpanAttribute && stream.Role == "values" {
					value.HotProducerStreamID = stream.ProducerStreamID
				}
			}
		}, "not the active hot-values stream"},
		{"conflicting generation", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			plan.Streams[len(plan.Streams)-1].SourceCutoff.Value++
		}, "lifecycle mode differs"},
		{"zero generation", func(_ *ProducerStateRetirement, plan *buildPlanDocumentJSON) {
			for index := range plan.Streams {
				plan.Streams[index].SourceCutoff.Value = 0
			}
		}, "is invalid"},
		{"different lineage anchor", func(value *ProducerStateRetirement, _ *buildPlanDocumentJSON) {
			value.LineageAnchorRevision--
		}, "not its own lineage anchor"},
		{"different anchor digest", func(value *ProducerStateRetirement, _ *buildPlanDocumentJSON) {
			value.LineageAnchorActivationSHA256 = testDigest("different activation")
		}, "not its own lineage anchor"},
		{"different activation depth", func(value *ProducerStateRetirement, _ *buildPlanDocumentJSON) {
			value.ActiveRevisionsSinceAnchor++
		}, "activation depth differs"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := physicalSnapshotRetirementFixture(t)
			plan := retirementContractPlan(t, value)
			test.mutate(&value, &plan)
			// Recompute both digests so structural failures cannot be hidden
			// behind stale checksums. Stream order is deliberately preserved.
			setRetirementPlan(t, &value, plan)
			if err := validateProducerRetirement(value); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("want %q, got %v", test.want, err)
			}
		})
	}
	for _, digest := range []string{"build lease", "retirement"} {
		t.Run("corrupt "+digest+" digest", func(t *testing.T) {
			value := physicalSnapshotRetirementFixture(t)
			want := "retirement digest does not match"
			if digest == "build lease" {
				value.BuildLeaseSHA256 = testDigest("corrupted lease")
				value.RetirementSHA256 = producerRetirementSHA256(value)
				want = "build plan digest does not match build lease"
			} else {
				value.RetirementSHA256 = testDigest("corrupted retirement")
			}
			if err := validateProducerRetirement(value); err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("want %q, got %v", want, err)
			}
		})
	}
}
