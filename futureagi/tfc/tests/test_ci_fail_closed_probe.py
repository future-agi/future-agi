"""THROWAWAY: proves the CI gates fail closed. Reverted in the next commit."""


def test_the_backend_gate_fails_closed():
    print("throwaway lint violation")
    assert 1 == 2, "throwaway failure: Backend Tests Pass must be red"
