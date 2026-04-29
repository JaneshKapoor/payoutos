import { formatRupees, relativeTime } from "../format.js";

const STATE_STYLE = {
  pending: "bg-zinc-800 text-zinc-200",
  processing: "bg-blue-900/60 text-blue-200 animate-pulse",
  completed: "bg-emerald-900/60 text-emerald-200",
  failed: "bg-red-900/60 text-red-200",
};

export default function PayoutTable({ payouts }) {
  return (
    <div className="border border-zinc-800 rounded overflow-hidden">
      <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-200">Payouts</h2>
        <span className="text-xs text-zinc-500">live · polling every 2s</span>
      </div>
      {payouts && payouts.length > 0 ? (
        <table className="w-full text-sm">
          <thead className="text-xs text-zinc-500 uppercase">
            <tr className="border-b border-zinc-800">
              <th className="text-left px-4 py-2 font-medium">id</th>
              <th className="text-right px-4 py-2 font-medium">amount</th>
              <th className="text-left px-4 py-2 font-medium">state</th>
              <th className="text-right px-4 py-2 font-medium">attempts</th>
              <th className="text-right px-4 py-2 font-medium">created</th>
            </tr>
          </thead>
          <tbody>
            {payouts.map((p) => (
              <tr key={p.id} className="border-b border-zinc-900 last:border-b-0">
                <td className="px-4 py-2 font-mono text-xs text-zinc-400">
                  {p.id.slice(0, 8)}…
                </td>
                <td className="px-4 py-2 text-right font-mono">
                  {formatRupees(p.amount_paise)}
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium uppercase tracking-wider ${
                      STATE_STYLE[p.state] || "bg-zinc-800"
                    }`}
                  >
                    {p.state}
                  </span>
                  {p.failure_detail && (
                    <span className="ml-2 text-xs text-red-300/80">{p.failure_detail}</span>
                  )}
                </td>
                <td className="px-4 py-2 text-right font-mono text-zinc-400">{p.attempts}</td>
                <td className="px-4 py-2 text-right text-xs text-zinc-500">
                  {relativeTime(p.created_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="px-4 py-6 text-zinc-500 text-sm">no payouts yet — request one above.</div>
      )}
    </div>
  );
}
