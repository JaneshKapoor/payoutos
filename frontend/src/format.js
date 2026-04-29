// All money on the wire is paise (INR * 100), as integers. The UI only
// converts to rupees at the *very* edge — `formatRupees` for display,
// `parseRupeesToPaise` for input.

export function formatRupees(paise) {
  if (paise == null || Number.isNaN(paise)) return "—";
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const remainingPaise = abs % 100;
  const formattedRupees = rupees.toLocaleString("en-IN");
  return `${sign}₹${formattedRupees}.${String(remainingPaise).padStart(2, "0")}`;
}

export function parseRupeesToPaise(input) {
  // Accepts "1234", "1234.5", "1234.56". Rejects "1234.567" — caller
  // should surface the error.
  const trimmed = String(input).trim();
  if (!trimmed) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return null;
  const [r, p = ""] = trimmed.split(".");
  const paise = parseInt(r, 10) * 100 + parseInt(p.padEnd(2, "0"), 10);
  return paise;
}

export function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.round((now - then) / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  return `${d}d ago`;
}
