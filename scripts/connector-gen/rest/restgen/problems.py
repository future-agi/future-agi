"""Problem collection: the generator reports every problem it finds in one
run, each naming its file and where in it, then refuses to write."""

from __future__ import annotations


class GenError(Exception):
    """One or more problems; the message lists each on its own line."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


class Problems:
    """Collects problems for one input; raise_if_any() ends the run."""

    def __init__(self, where: str):
        self.where = where
        self.items: list[str] = []

    def add(self, message: str, where: str | None = None) -> None:
        self.items.append(f"{where or self.where}: {message}")

    def extend(self, other: Problems) -> None:
        self.items.extend(other.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def raise_if_any(self) -> None:
        if self.items:
            raise GenError(self.items)
