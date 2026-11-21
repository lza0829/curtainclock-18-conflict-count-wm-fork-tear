import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

const actionLabel = { open: "开窗", close: "关窗" };
const resultLabel = { success: "成功", conflict: "冲突", rejected: "拒绝" };

export default function Audit() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      const code = filter.trim();
      setRows(await api.audit(code || undefined));
    } catch (e) {
      setErr(e.message);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="panel">
      <h2>审计事件（只追加）</h2>
      <div className="msg">
        口径：开窗成败与关窗各落一行，永不更新/物理删除；可按批窗过滤
      </div>
      <div style={{ display: "flex", gap: 8, margin: "10px 0", flexWrap: "wrap" }}>
        <input
          value={filter}
          placeholder="按批窗编码过滤"
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: "1 1 260px", minWidth: 220 }}
        />
        <button className="secondary" onClick={load}>
          刷新
        </button>
      </div>
      {err && <div className="msg err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>事件 #</th>
            <th>动作</th>
            <th>批窗</th>
            <th>租约</th>
            <th>结果</th>
            <th>明细</th>
            <th>时间（倒序）</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={7}>暂无审计事件</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{actionLabel[r.action] || r.action}</td>
              <td>{r.window_code}</td>
              <td>{r.lease_no || "—"}</td>
              <td>{resultLabel[r.result] || r.result}</td>
              <td>{r.detail}</td>
              <td>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="msg">当前列表行数：{rows.length}</div>
    </section>
  );
}
