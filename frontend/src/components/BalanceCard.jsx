import { formatRupees } from "../format.js";

function Tile({ label, paise, accent }) {
  return (
    <div className="flex-1 bg-zinc-900/60 border border-zinc-800 rounded p-4">
      <div className="text-xs text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className={`mt-1 text-2xl font-mono ${accent || "text-zinc-100"}`}>
        {formatRupees(paise)}
      </div>
    </div>
  );
}

export default function BalanceCard({ balance }) {
  if (!balance) {
    return (
      <div className="border border-zinc-800 rounded p-4 text-zinc-500 text-sm">
        loading balance…
      </div>
    );
  }
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      <Tile label="Available" paise={balance.available_paise} accent="text-emerald-400" />
      <Tile label="Held by pending payouts" paise={balance.held_paise} accent="text-amber-400" />
      <Tile label="Lifetime credits" paise={balance.lifetime_credits_paise} />
    </div>
  );
}
