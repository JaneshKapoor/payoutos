import { formatRupees, relativeTime } from "../format.js";

const KIND_LABEL = {
  credit_customer_payment: "Customer payment",
  debit_payout_hold: "Payout hold",
  credit_payout_reversal: "Payout reversal",
};

export default function LedgerTable({ entries }) {
  return (
    <div className="border border-zinc-800 rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-200">Ledger</h2>
        <p className="text-xs text-zinc-500">
          append-only · balance = SUM(amount_paise)
        </p>
      </div>
      {entries && entries.length > 0 ? (
        <table className="w-full text-sm">
          <thead className="text-xs text-zinc-500 uppercase">
            <tr className="border-b border-zinc-800">
              <th className="text-left px-4 py-2 font-medium">kind</th>
              <th className="text-left px-4 py-2 font-medium">description</th>
              <th className="text-right px-4 py-2 font-medium">amount</th>
              <th className="text-right px-4 py-2 font-medium">when</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => {
              const positive = e.amount_paise > 0;
              return (
                <tr key={e.id} className="border-b border-zinc-900 last:border-b-0">
                  <td className="px-4 py-2 text-zinc-300 text-xs">
                    {KIND_LABEL[e.kind] || e.kind}
                  </td>
                  <td className="px-4 py-2 text-zinc-400 text-xs">{e.description || "—"}</td>
                  <td
                    className={`px-4 py-2 text-right font-mono ${
                      positive ? "text-emerald-300" : "text-red-300"
                    }`}
                  >
                    {positive ? "+" : ""}
                    {formatRupees(e.amount_paise)}
                  </td>
                  <td className="px-4 py-2 text-right text-xs text-zinc-500">
                    {relativeTime(e.created_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <div className="px-4 py-6 text-zinc-500 text-sm">no ledger entries.</div>
      )}
    </div>
  );
}
