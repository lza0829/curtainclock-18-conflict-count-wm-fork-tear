import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

const kindLabel = { room: "算力槽", teacher: "执行器", ready: "就绪" };

export default function Conflicts() {
  const [rows, setRows] = useState([]);
  const [count, setCount] = useState(null);
  const [filter, setFilter] = useState("");
  const [resourceFilter, setResourceFilter] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      const args = {
        window_code: filter.trim() || undefined,
        resource_code: resourceFilter.trim() || undefined,
      };
      // 列表与计数分别由两个同源端点返回，页脚直接展示二者是否对得上
      const [list, cnt] = await Promise.all([
        api.conflicts(args),
        api.conflictCount(args),
      ]);
      setRows(list);
      setCount(cnt.conflict_count);
    } catch (e) {
      setErr(e.message);
    }
  }, [filter, resourceFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const aligned = count === null || count === rows.length;

  return (
    <section className="panel">
      <h2>冲突事件（只追加）</h2>
      <div className="msg">
        口径：每次开窗失败插入新事件行；本页行数 = 同条件计数 = 总览冲突计数
        （全部直读 conflicts 表，无缓存旁路）
      </div>
      <div style={{ display: "flex", gap: 8, margin: "10px 0", flexWrap: "wrap" }}>
        <input
          value={filter}
          placeholder="按批窗编码过滤，如 W-DIM-DUP"
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: "1 1 220px", minWidth: 200 }}
        />
        <input
          value={resourceFilter}
          placeholder="按资源编码过滤，如 S-ETL-01"
          onChange={(e) => setResourceFilter(e.target.value)}
          style={{ flex: "1 1 220px", minWidth: 200 }}
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
      <div className={`msg${aligned ? "" : " err"}`}>
        当前列表行数：{rows.length}｜同条件计数：{count ?? "…"}
        {aligned ? "（同源一致）" : "（计数与列表不一致，禁止当作已收口）"}
      </div>
    </section>
  );
}
