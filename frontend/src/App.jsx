import { useEffect, useState } from "react";
import { api } from "./api.js";
import MerchantSelector from "./components/MerchantSelector.jsx";
import BalanceCard from "./components/BalanceCard.jsx";
import PayoutForm from "./components/PayoutForm.jsx";
import PayoutTable from "./components/PayoutTable.jsx";
import LedgerTable from "./components/LedgerTable.jsx";

const POLL_MS = 2000;

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [merchantId, setMerchantId] = useState(null);
  const [balance, setBalance] = useState(null);
  const [bankAccounts, setBankAccounts] = useState([]);
  const [payouts, setPayouts] = useState([]);
  const [ledger, setLedger] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listMerchants().then(({ merchants }) => {
      setMerchants(merchants);
      if (merchants.length > 0) setMerchantId(merchants[0].id);
    }).catch((e) => setError(e.message));
  }, []);

  async function refresh() {
    if (!merchantId) return;
    try {
      const [b, ba, p, l] = await Promise.all([
        api.getBalance(merchantId),
        api.getBankAccounts(merchantId),
        api.listPayouts(merchantId),
        api.getLedger(merchantId),
      ]);
      setBalance(b);
      setBankAccounts(ba.bank_accounts);
      setPayouts(p.payouts);
      setLedger(l.entries);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    if (!merchantId) return;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merchantId]);

  return (
    <div className="min-h-full">
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Playto Payout Engine</h1>
          <p className="text-xs text-zinc-500">Merchant dashboard · demo</p>
        </div>
        <MerchantSelector
          merchants={merchants}
          value={merchantId}
          onChange={setMerchantId}
        />
      </header>

      {error && (
        <div className="mx-6 mt-4 px-4 py-2 bg-red-950/50 border border-red-900 text-red-200 rounded text-sm font-mono">
          {error}
        </div>
      )}

      <main className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-6">
        <div className="lg:col-span-2 space-y-6">
          <BalanceCard balance={balance} />
          <PayoutTable payouts={payouts} />
          <LedgerTable entries={ledger} />
        </div>
        <div className="space-y-6">
          <PayoutForm
            merchantId={merchantId}
            bankAccounts={bankAccounts}
            balance={balance}
            onCreated={refresh}
          />
          <Footnote />
        </div>
      </main>
    </div>
  );
}

function Footnote() {
  return (
    <div className="text-xs text-zinc-500 leading-relaxed border border-zinc-800 rounded p-4 bg-zinc-900/50">
      <p className="mb-2 text-zinc-400 font-medium">How this demo behaves</p>
      <ul className="list-disc pl-4 space-y-1">
        <li>Submitting a payout holds funds immediately (available balance drops).</li>
        <li>Worker simulates the bank: 70% complete, 20% fail, 10% hang.</li>
        <li>Hung payouts are retried by a periodic scanner.</li>
        <li>Failed payouts return the held funds atomically.</li>
        <li>Each submit uses a fresh idempotency key. Browser refresh is safe.</li>
      </ul>
    </div>
  );
}
