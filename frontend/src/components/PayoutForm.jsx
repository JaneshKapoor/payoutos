import { useEffect, useState } from "react";
import { api, uuidv4 } from "../api.js";
import { formatRupees, parseRupeesToPaise } from "../format.js";

export default function PayoutForm({ merchantId, bankAccounts, balance, onCreated }) {
  const [amount, setAmount] = useState("");
  const [bankId, setBankId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState(null);
  // We mint the idempotency key when the form mounts (or after a
  // successful submit). If the user clicks "submit" twice quickly, both
  // clicks share the same key — server returns the same payout.
  const [idemKey, setIdemKey] = useState(uuidv4());

  useEffect(() => {
    if (bankAccounts && bankAccounts.length > 0 && !bankId) {
      setBankId(bankAccounts[0].id);
    }
  }, [bankAccounts, bankId]);

  async function onSubmit(e) {
    e.preventDefault();
    if (!merchantId || !bankId) return;
    const paise = parseRupeesToPaise(amount);
    if (paise == null || paise <= 0) {
      setFeedback({ kind: "error", text: "enter a valid rupee amount (e.g. 1234.56)" });
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.createPayout({
        merchantId,
        amountPaise: paise,
        bankAccountId: bankId,
        idempotencyKey: idemKey,
      });
      setFeedback({
        kind: "ok",
        text: `payout queued — ${formatRupees(result.amount_paise)} → ${result.id.slice(0, 8)}…`,
      });
      setAmount("");
      setIdemKey(uuidv4()); // fresh key for the next request
      onCreated?.();
    } catch (err) {
      setFeedback({
        kind: "error",
        text: err.message || "failed to create payout",
      });
    } finally {
      setSubmitting(false);
    }
  }

  const available = balance?.available_paise ?? 0;
  const requested = parseRupeesToPaise(amount) || 0;
  const overdraft = requested > available;

  return (
    <form
      onSubmit={onSubmit}
      className="border border-zinc-800 rounded p-4 bg-zinc-900/40 space-y-4"
    >
      <div>
        <h2 className="text-sm font-semibold text-zinc-200">Request a payout</h2>
        <p className="text-xs text-zinc-500 mt-0.5">
          available: <span className="font-mono text-zinc-300">{formatRupees(available)}</span>
        </p>
      </div>

      <label className="block">
        <span className="text-xs text-zinc-500">Amount (₹)</span>
        <input
          type="text"
          inputMode="decimal"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="0.00"
          className="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-zinc-500"
        />
      </label>

      <label className="block">
        <span className="text-xs text-zinc-500">Bank account</span>
        <select
          value={bankId}
          onChange={(e) => setBankId(e.target.value)}
          className="mt-1 w-full bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:border-zinc-500"
        >
          {bankAccounts?.map((b) => (
            <option key={b.id} value={b.id}>
              {b.account_holder_name} · {b.account_number_masked} · {b.ifsc}
            </option>
          ))}
        </select>
      </label>

      <div className="text-[10px] text-zinc-600 font-mono break-all">
        Idempotency-Key: {idemKey}
      </div>

      <button
        type="submit"
        disabled={submitting || !amount || overdraft}
        className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white text-sm font-medium rounded px-4 py-2 transition"
      >
        {submitting ? "submitting…" : overdraft ? "exceeds available balance" : "Request payout"}
      </button>

      {feedback && (
        <div
          className={`text-xs font-mono px-3 py-2 rounded ${
            feedback.kind === "ok"
              ? "bg-emerald-950/50 border border-emerald-900 text-emerald-200"
              : "bg-red-950/50 border border-red-900 text-red-200"
          }`}
        >
          {feedback.text}
        </div>
      )}
    </form>
  );
}
