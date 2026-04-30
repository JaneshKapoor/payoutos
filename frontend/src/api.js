// API client. The dashboard talks to Django via /api/v1/*. We use the
// X-Merchant-Id header as the demo "auth" — in a real product this
// would be a session cookie or signed token.

const API_HOST = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const BASE = `${API_HOST}/api/v1`;

function uuidv4() {
  if (window.crypto && window.crypto.randomUUID) {
    return window.crypto.randomUUID();
  }
  // Fallback for very old browsers; not crypto-safe but fine for an
  // idempotency key in a demo.
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
    (c ^ ((Math.random() * 16) | 0) >> (c / 4)).toString(16),
  );
}

async function request(path, { method = "GET", merchantId, body, idempotencyKey } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (merchantId) headers["X-Merchant-Id"] = merchantId;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new Error(data?.detail || data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.code = data?.error;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  listMerchants: () => request("/merchants"),
  getBalance: (merchantId) => request(`/merchants/${merchantId}/balance`),
  getLedger: (merchantId, limit = 25) =>
    request(`/merchants/${merchantId}/ledger?limit=${limit}`),
  getBankAccounts: (merchantId) =>
    request(`/merchants/${merchantId}/bank-accounts`),
  listPayouts: (merchantId, limit = 25) =>
    request(`/payouts/list?limit=${limit}`, { merchantId }),
  createPayout: ({ merchantId, amountPaise, bankAccountId, idempotencyKey }) =>
    request("/payouts", {
      method: "POST",
      merchantId,
      idempotencyKey: idempotencyKey || uuidv4(),
      body: { amount_paise: amountPaise, bank_account_id: bankAccountId },
    }),
};

export { uuidv4 };
