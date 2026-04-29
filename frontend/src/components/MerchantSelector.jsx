export default function MerchantSelector({ merchants, value, onChange }) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-zinc-500">merchant</label>
      <select
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        className="bg-zinc-900 border border-zinc-800 rounded px-3 py-1.5 text-sm font-mono focus:outline-none focus:border-zinc-600"
      >
        {merchants.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
    </div>
  );
}
