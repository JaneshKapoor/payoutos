# EXPLAINER

The five questions, with the actual code that answers them.

---

## 1. The Ledger

**The query that derives a merchant's balance:**

```python
# apps/ledger/services.py — get_balance()
rows = LedgerEntry.objects.filter(merchant_id=merchant_id)

available = rows.aggregate(s=Sum("amount_paise"))["s"] or 0

held = (
    Payout.objects.filter(
        merchant_id=merchant_id,
        state__in=[PayoutState.PENDING, PayoutState.PROCESSING],
    ).aggregate(s=Sum("amount_paise"))["s"]
    or 0
)
```

That's it. `available_paise` is a single SQL `SUM` over `ledger_entries.amount_paise`. No Python-side arithmetic on fetched rows.

**Why this model:**

The ledger is **append-only** with **signed integers**:

- `credit_customer_payment` → stored as `+amount`
- `debit_payout_hold`       → stored as `-amount`
- `credit_payout_reversal`  → stored as `+amount` (when a payout fails)

Storing the sign on the row means:

```
balance = SUM(amount_paise)        -- never breaks, always one query
```

The "credits − debits = balance" invariant the challenge calls out is not a thing we have to maintain — **it is the definition of balance** in this design. There is no `merchants.balance` column to drift away from the truth, because there is no balance column at all.

A check constraint on the table (`ledger_entry_sign_matches_kind`) makes sure nothing can insert a credit row with a negative amount or a debit row with a positive amount, so even direct SQL can't corrupt the invariant.

**Why ledger over `merchants.balance`:**

1. A balance column is one row that every credit and debit has to update — that's a hot row, contended under write load. The ledger is pure `INSERT`, never `UPDATE`.
2. Auditability: every paisa of difference between two balance snapshots has a row that explains it, with a foreign key to the payout (or other source) that caused it.
3. Failure recovery is simpler: failing a payout means inserting one reversal row, not figuring out what to add back to a column.

**Holds without a separate `held_balance` column:** because debits land in the ledger the moment the payout is created (not when it completes), the available balance already reflects the hold. The `held_paise` number on the dashboard is computed for display only — `SUM(amount_paise)` over payouts in `pending` or `processing`. It's never used in the affordability check.

---

## 2. The Lock

**The exact code that prevents two concurrent payouts from overdrawing:**

```python
# apps/payouts/services.py — request_payout()
with transaction.atomic():
    # 1. Lock the merchant row.
    merchant = Merchant.objects.select_for_update().get(id=merchant_id)

    # 2. Recompute balance from the ledger WHILE holding the lock.
    balance = get_balance(merchant.id)
    if balance.available_paise < amount_paise:
        raise InsufficientFunds(...)

    # 3. Insert payout + matching debit ledger row.
    payout = Payout.objects.create(...)
    LedgerEntry.objects.create(
        merchant=merchant,
        kind=LedgerEntryKind.DEBIT_PAYOUT_HOLD,
        amount_paise=-amount_paise,
        payout=payout,
        ...
    )
```

**The primitive it relies on:** Postgres' `SELECT ... FOR UPDATE` row-level lock, taken inside a transaction. The merchant row is the lock anchor — the **same** row is locked for every payout that touches that merchant, and the lock is released on commit/rollback.

**Walkthrough of the race the challenge describes** (merchant has 100 rupees, two simultaneous 60-rupee requests):

1. Request A and request B both arrive, hit the same gunicorn worker pool.
2. Both run `transaction.atomic()` and both ask for `select_for_update()` on the same merchant row.
3. Postgres serializes them: one transaction holds the row lock, the other blocks at the SELECT until the first commits.
4. Request A reads `available = 100_00`, sees `100_00 >= 60_00`, inserts payout + debit row, commits. Lock released.
5. Request B unblocks. `get_balance` is now re-executed — and because B is reading after A committed (and because we're at READ COMMITTED), it sees A's debit row. `available = 40_00`. `40_00 < 60_00` → `InsufficientFunds`.

The crucial detail: the balance read in step 4 happens **after** the lock is acquired and **before** the debit insert. If you flip those (read first, then lock), or skip the lock entirely, two requests can both see 100, both write 60, and you've overdrawn the merchant. That bug is the entire reason this lock exists.

**Why the lock is on `Merchant`, not `Payout`:** the contended resource is "this merchant's balance," not any specific payout row. Locking the merchant row serializes all balance modifications for that merchant while leaving payouts for other merchants free to run in parallel.

**Why not just rely on Django's `transaction.atomic()`:** transactions give you ACID, but ACID alone allows two concurrent transactions to both observe the pre-state, both decide the operation is fine, both write. The lock is what forces them to take turns.

The concurrency test (`apps/payouts/tests/test_concurrency.py`) actually races two real threads against this code path and asserts exactly one succeeds.

---

## 3. The Idempotency

**How the system knows it's seen a key before:**

```python
# apps/payouts/models.py
class IdempotencyKey(models.Model):
    merchant = models.ForeignKey(Merchant, ...)
    key = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(...)                    # in_flight | completed
    response_status_code = models.IntegerField(...)
    response_body = models.JSONField(...)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["merchant", "key"],
                name="idempotency_keys_unique_per_merchant",
            ),
        ]
```

The `(merchant, key)` unique constraint is what enforces dedup. Two flows feed into it:

1. **Fast path** (`_lookup_idempotency`): on every request, before doing any real work, we look up `(merchant_id, key)`. If a row exists, is within TTL, and has `status=completed`, we return the cached response and never touch the merchant row at all. Saves a lock acquisition for what would otherwise be a no-op.
2. **Slow path** (the `INSERT` inside `request_payout`): the first request to actually perform the payout inserts a fresh `IdempotencyKey` row in the same transaction as the `Payout` and the `LedgerEntry`. Either all three commit or none of them do.

**What happens if the first request is in-flight when the second arrives:**

The fast path lookup finds a row with `status=in_flight` and raises `IdempotencyKeyInFlight` → HTTP `409 Conflict`. We deliberately do **not** block the second caller — that would tie up an HTTP worker for the duration of the first request. The merchant retries.

(The challenge spec doesn't say "block and wait." Telling the client `409` and letting them retry is correct, idiomatic, and what Stripe's idempotency layer also does.)

**The race I had to think hardest about:**

Two requests with the same `(merchant, key)` race past the fast-path lookup before either has inserted the `IdempotencyKey` row. Both enter `request_payout`. Now:

- Both take the merchant row lock — but Postgres serializes them.
- The first commits the payout, the debit, and the `IdempotencyKey` row.
- The second resumes, reads the balance (sees the first's debit), inserts its own payout, then tries to insert *its* `IdempotencyKey` row — and **the unique constraint fires**. `IntegrityError`.
- We catch the `IntegrityError`, re-run the fast-path lookup, find the now-committed row, and return the cached response.

The result: the merchant gets one payout, two `IdempotencyKey` lookups, zero duplicate writes. The unique index is the actual safety net; the fast-path lookup is a happy-path optimization.

**Per-merchant scoping** falls out of the unique constraint being on `(merchant, key)` rather than on `key` alone. Two merchants can each use the UUID `abc-123` without colliding.

**24-hour TTL:**

```python
# apps/payouts/services.py
ttl = settings.IDEMPOTENCY_TTL_HOURS * 3600
if record.is_expired(ttl):
    record.delete()
    return None
```

A row older than 24h is treated as if it never existed. A `purge_expired_idempotency_keys` Celery task can sweep them periodically.

The idempotency tests (`apps/payouts/tests/test_idempotency.py`) cover all three behaviors: same key+body returns same payout, same key+different body raises conflict, same key across merchants both succeed.

---

## 4. The State Machine

**Where `failed → completed` is blocked:**

```python
# apps/payouts/state_machine.py
_LEGAL: dict[str, frozenset[str]] = {
    PayoutStateConst.PENDING:    frozenset({PROCESSING, FAILED}),
    PayoutStateConst.PROCESSING: frozenset({COMPLETED, FAILED}),
    PayoutStateConst.COMPLETED:  frozenset(),  # terminal
    PayoutStateConst.FAILED:     frozenset(),  # terminal
}

def assert_can_transition(current: str, target: str) -> None:
    if target not in _LEGAL.get(current, frozenset()):
        raise IllegalStateTransition(current, target)
```

`_LEGAL[FAILED]` is the empty set. The check is a single set membership: if it's not in there, we raise.

**Where the check actually runs** — every state mutation goes through one chokepoint:

```python
# apps/payouts/services.py — transition_to()
with transaction.atomic():
    payout = Payout.objects.select_for_update().get(id=payout_id)
    assert_can_transition(payout.state, target_state)
    # ... mutate state, optionally insert reversal entry, save
```

Two reasons this lives inside `transaction.atomic()` with a `select_for_update`:

1. **Atomicity**: the legality check and the `UPDATE` happen together. There is no window where a parallel worker can see the new state without the corresponding ledger entry, or vice versa.
2. **Mutual exclusion between workers**: two Celery workers can theoretically pick up the same payout (we deliberately don't trust the queue to dedupe). The row lock means only one of them passes the check; the other reads the post-state and the state machine raises.

**Why a class instead of inlining `if state in {...}`:**

- Centralization — you grep `assert_can_transition` and find every callsite.
- The set of legal moves fits on one screen, so a reviewer can audit it at a glance.
- Tests (`test_state_machine.py`) drive every illegal transition through the same function and confirm they all raise.

**Atomic refund on `failed`:** the same `transition_to` that flips state to `FAILED` *also* inserts the `credit_payout_reversal` ledger entry inside the same transaction:

```python
elif target_state == PayoutState.FAILED:
    payout.state = PayoutState.FAILED
    ...
    LedgerEntry.objects.create(
        merchant_id=payout.merchant_id,
        kind=LedgerEntryKind.CREDIT_PAYOUT_REVERSAL,
        amount_paise=payout.amount_paise,
        payout=payout,
        ...
    )
```

If the reversal write blows up, the state flip rolls back too. There is no path where a payout shows `failed` without the reversal entry sitting alongside it, or vice versa.

---

## 5. The AI Audit

**The wrong code AI gave me, and what I caught.**

I asked the model for the balance check + debit insert. The first cut looked like:

```python
# WRONG — what AI initially produced
def request_payout(merchant_id, amount_paise, ...):
    with transaction.atomic():
        balance = LedgerEntry.objects.filter(
            merchant_id=merchant_id
        ).aggregate(s=Sum("amount_paise"))["s"] or 0

        if balance < amount_paise:
            raise InsufficientFunds(...)

        payout = Payout.objects.create(...)
        LedgerEntry.objects.create(
            merchant_id=merchant_id,
            kind=LedgerEntryKind.DEBIT_PAYOUT_HOLD,
            amount_paise=-amount_paise,
            payout=payout,
        )
```

Three things were wrong with it, in order of severity:

**(a) No `select_for_update`.** This is the textbook race. `transaction.atomic()` gives ACID, but at READ COMMITTED (the Postgres default and Django's default) two concurrent transactions can both run the `aggregate(...)`, both see `balance = 100_00`, both pass the `< amount_paise` check, both insert their debit, and commit. Net result: a 100-rupee balance has produced 120 rupees of payouts. The merchant is overdrawn and we owe the bank.

The fix was to take a row lock on the merchant *before* reading the balance:

```python
merchant = Merchant.objects.select_for_update().get(id=merchant_id)
balance = get_balance(merchant.id)
```

Now Postgres serializes the two transactions on the merchant row, so the second one's aggregate runs after the first has committed and sees the lower balance.

**(b) `aggregate` returning `None` collapsed to `0`.** The `or 0` works for an unknown merchant, but it also masks the case where the merchant has zero entries because the merchant *doesn't exist*. I split that out: I `get()` the merchant first (raises `Merchant.DoesNotExist` → 404 / domain error) and only then aggregate.

**(c) The `aggregate` was inline in the view-level service.** Hard to reuse for the dashboard's balance read, where I also needed `held_paise`. I pulled it into `apps/ledger/services.get_balance` returning a typed `BalanceSnapshot`, so both the API balance endpoint and the affordability check share one source of truth. If we ever change how balance is computed, there is exactly one place to change.

**General principle the audit reinforced:**

AI is correct about syntax and library APIs almost always. It is consistently sloppy about anything that depends on **runtime semantics under concurrency** — locking, isolation levels, the difference between transaction boundaries and lock scope. Any time the model writes `transaction.atomic()` near a check-then-write, I now reach for the locking call myself and verify it manually instead of trusting that the code "looks atomic."
