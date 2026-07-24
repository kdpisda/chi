"""Hard budget caps with optional persistence to the run store."""

from chi.store.db import Store, utcnow


class BudgetExceededError(Exception):
    """Raised when a call would exceed the run or role budget cap."""


class BudgetTracker:
    """Tracks USD spend against a total cap and optional per-role caps."""

    def __init__(
        self,
        total_usd: float,
        per_role: dict[str, float] | None = None,
        store: Store | None = None,
        run_id: str | None = None,
    ) -> None:
        self.total_usd = total_usd
        self.per_role = per_role or {}
        self._spent: dict[str, float] = {}
        self._store = store
        self._run_id = run_id

    @property
    def spent(self) -> float:
        """Total USD spent across all roles."""
        return sum(self._spent.values())

    def spent_for(self, role: str) -> float:
        """USD spent by one role."""
        return self._spent.get(role, 0.0)

    def check(self, role: str = "default") -> None:
        """Raise BudgetExceededError if the total or role cap is already reached."""
        reason = None
        if self.spent >= self.total_usd:
            reason = f"total budget ${self.total_usd:.2f} exhausted (spent ${self.spent:.4f})"
        elif role in self.per_role and self.spent_for(role) >= self.per_role[role]:
            reason = (
                f"role '{role}' budget ${self.per_role[role]:.2f} exhausted"
                f" (spent ${self.spent_for(role):.4f})"
            )
        if reason is None:
            return
        if self._store is not None and self._run_id is not None:
            from chi.store import events

            events.append_event(
                self._store, self._run_id, events.BUDGET_BLOCK, payload={"reason": reason}
            )
        raise BudgetExceededError(reason)

    def record(self, cost_usd: float, role: str = "default") -> None:
        """Record spend for a role; persists to the budgets table when attached."""
        self._spent[role] = self.spent_for(role) + cost_usd
        if self._store is not None and self._run_id is not None:
            self._store.execute(
                "INSERT INTO budgets (scope, run_id, cap_usd, spent_usd, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(scope, run_id) DO UPDATE SET spent_usd=excluded.spent_usd,"
                " updated_at=excluded.updated_at",
                (f"role:{role}", self._run_id,
                 self.per_role.get(role, self.total_usd), self.spent_for(role), utcnow()),
            )
