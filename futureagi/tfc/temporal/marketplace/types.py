from dataclasses import dataclass


@dataclass
class ConsumerState:
    total_events_processed: int = 0


@dataclass
class DrainResult:
    events_processed: int = 0
    had_events: bool = False
