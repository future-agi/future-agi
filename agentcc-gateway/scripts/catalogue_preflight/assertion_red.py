"""Separate synthetic physical-routing assertion-red harness; never auto-run.

The accepted native supervisor owns all compilation and child launch. This
module authors one exact compiler input and validates green/red/restored
receipts. It never edits a worktree, guard, TLS code, fixture or assertion.
"""
import hashlib
import json
from pathlib import Path

SIGNATURE = "func (d *Dialer) check(host, resolved string, plaintext bool) error {\n"
MUTATION = "\t// Catalogue assertion-red: disposable compiler input only.\n\tif true { return nil }\n"


def mutated_check(source, expected_sha256):
    if hashlib.sha256(source).hexdigest() != expected_sha256:
        raise ValueError("source policy digest changed")
    text = source.decode()
    if text.count(SIGNATURE) != 1 or MUTATION in text:
        raise ValueError("policy function identity changed")
    result = text.replace(SIGNATURE, SIGNATURE + MUTATION, 1).encode()
    if result.replace(MUTATION.encode(), b"", 1) != source:
        raise AssertionError("mutation changed more than the policy entry")
    return result


def run_triad(boundary, source_file, expected_sha256, output, checks):
    if boundary is None:
        raise RuntimeError("accepted OS1-OS6 supervisor required")
    boundary.require_before_imports(tuple("OS" + str(i) for i in range(1, 7)))
    source = Path(source_file).read_bytes()
    mutant = mutated_check(source, expected_sha256)
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    # A compiler input, NOT a source checkout/edit. The unchanged frozen Go
    # guard overlay must be composed without collisions by the supervisor.
    mutated = output / "egress-policy-mutation.go"
    with mutated.open("xb") as stream:
        stream.write(mutant)
    record = {"original_sha256": expected_sha256,
              "mutated_sha256": hashlib.sha256(mutant).hexdigest(),
              "only_function": "egress.(*Dialer).check", "productAccepted": False}
    (output / "mutation.json").write_text(json.dumps(record, sort_keys=True, indent=2))
    receipts = []
    for arm in ("green", "red", "restored"):
        # Each uses the same static intent and fixture/guard/assertion inputs,
        # fresh DB/listeners/registrations, and the same metadata DNS answer.
        # No 'red expected result' is passed to the original route assertions.
        result = boundary.run_synthetic_arm(arm=arm, case="synthetic-metadata",
            output=output / arm,
            compiler_input=(str(mutated) if arm == "red" else None),
            original_source=str(Path(source_file).resolve()))
        receipt = result["receipt"]
        if result["command_exit"] != (1 if arm == "red" else 0):
            raise AssertionError("missing real assertion failure or failed green")
        if result["source_sha256"] != expected_sha256 or not result["frozen_guards_unchanged"]:
            raise AssertionError("source or outer guard changed")
        receipts.append(receipt)
    return checks.assertion_red(*receipts)
