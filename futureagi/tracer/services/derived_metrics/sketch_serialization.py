"""Lossless receipt normalization for uniqCombined64's unordered hash-set state.

ClickHouse 25.8 CombinedCardinalityEstimator MEDIUM serializes a VarUInt length
and UInt64 hashes in hash-table iteration order. Deserialization reinserts them;
collision chains can change iteration order without changing even one hash.
Sort ONLY those fixed-width hashes, retaining the representation tag, size, all
scalar bytes and the entire other sketch. SMALL and LARGE remain byte-exact.
This is full state equality (including merge behavior), not cardinality equality.

Wire contract: ClickHouse v25.8.33.6-lts, CombinedCardinalityEstimator.h,
HashTable/HashTable.h and AggregateFunctionUniqCombined.h. Unknown layouts fail
closed. This never decodes telemetry or reconstructs a sketch from its estimate.
"""


def _varuint(data, offset):
    value = 0
    for shift in range(0, 64, 7):
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("invalid sketch state length")


def canonical_sketch_row(table, encoded):
    """Return replayable RowBinary, changing only unordered MEDIUM hash order."""
    names = table.columns
    if "uniq_state" not in names:
        return encoded
    if (
        table.schema[names.index("uniq_state")][1]
        != "AggregateFunction(uniqCombined64, String)"
    ):
        raise ValueError("unsupported metric sketch state")
    data = bytes.fromhex(encoded)
    offset = 0
    widths = {"UUID": 16, "UInt64": 8, "Float64": 8, "UInt32": 4}
    try:
        for name, kind in table.schema:
            if name == "uniq_state":
                break
            if kind in {"String", "LowCardinality(String)"}:
                size, offset = _varuint(data, offset)
                offset += size
            elif kind == "DateTime('UTC')":
                offset += 4
            elif kind in widths:
                offset += widths[kind]
            else:
                raise ValueError("unsupported metric sketch row layout")
        tag = data[offset]
        if tag not in {1, 2, 3}:
            raise ValueError("unsupported metric sketch representation")
        if tag != 2:
            return encoded
        count, start = _varuint(data, offset + 1)
        end = start + 8 * count
        # uniqCombined64 defaults to K=17 and transitions to HLL at 2^(17-5).
        if not 16 <= count <= 4096 or end > len(data):
            raise ValueError("invalid metric sketch hash set")
        hashes = [data[i : i + 8] for i in range(start, end, 8)]
        if len(set(hashes)) != count:
            raise ValueError("duplicate metric sketch hash")
        return (data[:start] + b"".join(sorted(hashes)) + data[end:]).hex().upper()
    except IndexError:
        raise ValueError("truncated metric sketch row") from None
