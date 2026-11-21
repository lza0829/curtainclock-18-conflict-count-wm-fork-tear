import { useEffect, useState } from "react";
import { api } from "../api";

export default function Resources() {
  const [slots, setRooms] = useState([]);
  const [executors, setTeachers] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([api.slots(), api.executors()])
      .then(([r, t]) => {
        setRooms(r);
        setTeachers(t);
      })
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;

  return (
    <>
      <section className="panel">
        <h2>算力槽</h2>
        <table>
          <thead>
            <tr>
              <th>编码</th>
              <th>名称</th>
              <th>容量</th>
              <th>状态</th>
              <th>冲突角标</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((r) => (
              <tr key={r.code}>
                <td>{r.code}</td>
                <td>{r.name}</td>
                <td>{r.capacity}</td>
                <td>{r.active ? "启用" : "停用"}</td>
                <td>
                  <span className={`tag ${r.conflict_count ? "bad" : "done"}`}>
                    {r.conflict_count || 0}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="msg">
          角标 = conflicts 表中该资源的事件行数，与冲突页按资源筛选同源
        </div>
      </section>
      <section className="panel">
        <h2>执行器</h2>
        <table>
          <thead>
            <tr>
              <th>编码</th>
              <th>名称</th>
              <th>作业类型</th>
              <th>状态</th>
              <th>冲突角标</th>
            </tr>
          </thead>
          <tbody>
            {executors.map((t) => (
              <tr key={t.code}>
                <td>{t.code}</td>
                <td>{t.name}</td>
                <td>{t.subject}</td>
                <td>{t.active ? "启用" : "停用"}</td>
                <td>
                  <span className={`tag ${t.conflict_count ? "bad" : "done"}`}>
                    {t.conflict_count || 0}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="msg">
          角标 = conflicts 表中该资源的事件行数，与冲突页按资源筛选同源
        </div>
      </section>
    </>
  );
}
