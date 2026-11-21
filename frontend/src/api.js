async function req(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    // 409 开窗失败回执是结构化对象（含 conflict_ids / 同源计数），保留在 err.payload
    const detail = data?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : detail?.error || data?.error || res.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.payload = typeof detail === "object" && detail !== null ? detail : data;
    throw err;
  }
  return data;
}

export const api = {
  dashboard: () => req("/dashboard"),
  slots: () => req("/slots"),
  executors: () => req("/executors"),
  windows: () => req("/windows"),
  leases: () => req("/leases"),
  conflicts: (window_code) =>
    req(
      window_code
        ? `/conflicts?window_code=${encodeURIComponent(window_code)}`
        : "/conflicts"
    ),
  conflictCount: (window_code) =>
    req(
      window_code
        ? `/conflicts/count?window_code=${encodeURIComponent(window_code)}`
        : "/conflicts/count"
    ),
  audit: (window_code) =>
    req(
      window_code
        ? `/audit?window_code=${encodeURIComponent(window_code)}`
        : "/audit"
    ),
  openLease: (window_code) =>
    req("/leases/open", { method: "POST", body: JSON.stringify({ window_code }) }),
  closeLease: (lease_no) =>
    req("/leases/close", { method: "POST", body: JSON.stringify({ lease_no }) }),
};
