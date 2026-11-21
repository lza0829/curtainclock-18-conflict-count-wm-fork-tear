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
  conflicts: ({ window_code, resource_code } = {}) => {
    const q = new URLSearchParams();
    if (window_code) q.set("window_code", window_code);
    if (resource_code) q.set("resource_code", resource_code);
    const qs = q.toString();
    return req(qs ? `/conflicts?${qs}` : "/conflicts");
  },
  conflictCount: ({ window_code, resource_code } = {}) => {
    const q = new URLSearchParams();
    if (window_code) q.set("window_code", window_code);
    if (resource_code) q.set("resource_code", resource_code);
    const qs = q.toString();
    return req(qs ? `/conflicts/count?${qs}` : "/conflicts/count");
  },
  conflictsByResource: () => req("/conflicts/by-resource"),
  watermark: () => req("/watermark"),
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
