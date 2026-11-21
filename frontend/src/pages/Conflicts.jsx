import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

const kindLabel = { room: "算力槽", teacher: "执行器", ready: "就绪" };

export default function Conflicts() {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      const code = filter.trim();
      setRows(await api.conflicts(code || undefined));
    } catch (e) {
      setErr(e.message);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="panel">
      <h2>冲突事件（只追加）</h2>
      <div className="msg">
        口径：每次开窗失败插入新事件行；本页行数 = 总览冲突计数（同源于 conflicts 表）
      </div>
      <div style={{ display: "flex", gap: 8, margin: "10px 0", flexWrap: "wrap" }}>
        <input
          value={filter}
          placeholder="按批窗编码过滤，如 W-DIM-DUP"
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
            <th>类型</th>
            <th>批窗</th>
            <th>资源</th>
            <th>原因</th>
            <th>时间（倒序）</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={6}>暂无冲突</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{kindLabel[r.kind] || r.kind}</td>
              <td>{r.window_code}</td>
              <td>{r.resource_code}</td>
              <td>{r.reason}</td>
              <td>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="msg">当前列表行数：{rows.length}</div>
    </section>
  );
}
