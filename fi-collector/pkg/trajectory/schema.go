package trajectory

import (
	"sort"
	"strconv"
	"strings"
)

// ItemsTable is the ClickHouse table holding deduplicated items.
const ItemsTable = "trajectory_items"

// ColumnContract is one column the collector writes, with the exact type
// ClickHouse reports for it in system.columns.
type ColumnContract struct {
	Name string
	Type string
}

// SpansPointerColumns are the six pointer columns 040_trajectory_items.sql
// adds to spans. A span row carrying pointers is only written while every one
// of them exists with exactly this type.
var SpansPointerColumns = []ColumnContract{
	{"input_item_hashes", "Array(String)"},
	{"output_item_hashes", "Array(String)"},
	{"tool_def_hashes", "Array(String)"},
	{"item_scope_id", "String"},
	{"payload_encoding", "LowCardinality(String)"},
	{"payload_envelope", "String"},
}

// ItemsColumns are the columns of trajectory_items, as 040 defines them.
var ItemsColumns = []ColumnContract{
	{"project_id", "UUID"},
	{"scope_id", "String"},
	{"item_hash", "FixedString(32)"},
	{"item_type", "LowCardinality(String)"},
	{"role", "LowCardinality(String)"},
	{"content", "String"},
	{"persisted_at", "DateTime64(6, 'UTC')"},
}

// SchemaProblems compares the columns ClickHouse reports (name -> type) with
// a contract and returns one entry per missing column ("name") or wrong type
// ("name: got T, want U"), sorted. Empty means the contract holds.
func SchemaProblems(contract []ColumnContract, actual map[string]string) []string {
	var problems []string
	for _, col := range contract {
		got, ok := actual[col.Name]
		switch {
		case !ok:
			problems = append(problems, col.Name)
		case normalizeType(got) != normalizeType(col.Type):
			problems = append(problems, col.Name+": got "+got+", want "+col.Type)
		}
	}
	sort.Strings(problems)
	return problems
}

// normalizeType ignores whitespace and the quoting style of a time zone, so
// DateTime64(6,'UTC') and DateTime64(6, 'UTC') compare equal.
func normalizeType(t string) string {
	t = strings.ReplaceAll(t, `"`, "'")
	return strings.Join(strings.Fields(t), "")
}

// TableTopology is what the gate needs to know about one table's engine.
type TableTopology struct {
	// Exists is false when system.tables has no such table.
	Exists bool
	// Engine is system.tables.engine, e.g. ReplacingMergeTree,
	// ReplicatedReplacingMergeTree or Distributed.
	Engine string
	// TotalReplicas and ActiveReplicas come from system.replicas; they are
	// only read for Replicated* engines.
	TotalReplicas  int
	ActiveReplicas int
}

// Replicated reports whether the engine is Keeper-replicated.
func (t TableTopology) Replicated() bool { return strings.HasPrefix(t.Engine, "Replicated") }

// Distributed reports whether the engine is a Distributed facade.
func (t TableTopology) Distributed() bool { return t.Engine == "Distributed" }

// MergeTreeFamily reports whether the engine stores rows locally.
func (t TableTopology) MergeTreeFamily() bool { return strings.HasSuffix(t.Engine, "MergeTree") }

// InsertQuorum is the insert_quorum for the trajectory_items insert:
//   - plain MergeTree family: "auto" (ClickHouse ignores it on a
//     non-replicated table);
//   - Replicated with one replica: "1" (25.3's automatic quorum can time out
//     on a one-replica table);
//   - Replicated with more replicas: every replica (TotalReplicas). An item
//     is acknowledged only once each replica has it, so a reader on any
//     replica never sees a span before the items it points at, and a replica
//     that is down makes the insert fail and the drain write inline.
//
// ok is false for a topology the gate does not support.
func (t TableTopology) InsertQuorum() (quorum string, ok bool) {
	switch {
	case !t.Exists || t.Distributed() || !t.MergeTreeFamily():
		return "", false
	case !t.Replicated():
		return "auto", true
	case t.TotalReplicas <= 0:
		return "", false
	default:
		return strconv.Itoa(t.TotalReplicas), true
	}
}
