------------------------ MODULE PactExtractionExample ------------------------
(*
 * Concrete instantiation of Pact's extraction model for TLC verification.
 *
 * Codebase being modeled (a simplified pact-like tool):
 *
 *   def process(x):          contracts: requires "x"
 *       validate(x)
 *
 *   def validate():           contracts: no required args
 *       pass
 *
 *   def route(path, method):  contracts: requires "path", "method"
 *       process(path)
 *
 *   def main():
 *       route("/api", "GET")
 *       validate()
 *
 * Call sites:
 *   s1: main -> route("/api", "GET")       provides: path, method
 *   s2: route -> process(path)             provides: x      (path renamed)
 *   s3: main -> validate()                 provides: {}     (no required args)
 *
 * Expected TLC result:
 *   - process: safely extractable (s2 provides "x" ✓)
 *   - validate: safely extractable (no required args ✓)
 *   - route: safely extractable (s1 provides "path","method" ✓)
 *   ExtractionConfluent: TLC finds no ordering that blocks another extraction
 *)

EXTENDS TLC, FiniteSets

ArgNames == {"x", "path", "method"}

ContractImpl == [
    process  |-> {"x"},
    validate |-> {},
    route    |-> {"path", "method"}
]

CallSites3Impl == {
    [caller |-> "main",  callee |-> "route",    site |-> "s1"],
    [caller |-> "route", callee |-> "process",  site |-> "s2"],
    [caller |-> "main",  callee |-> "validate", site |-> "s3"]
}

ProvisionImpl == [
    s1 |-> {"path", "method"},
    s2 |-> {"x"},
    s3 |-> {}
]

=============================================================================
