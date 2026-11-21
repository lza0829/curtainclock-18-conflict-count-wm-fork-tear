import { useEffect, useState } from "react";
import { api } from "../api";

export default function Home() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .dashboard()
      .then(setD)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;
  if (!d) return <div className="panel">加载中…</div>;

  const wm = d.watermark || {};
  const aligned =
    d.conflict_count === d.conflict_rows_visible && d.watermarks_consistent === true;

  return (
    <section className="panel">
      <h2>今日水位 · {d.fixture_day}</h2>
      <div className="stats">
        <div className="stat">
          在途租约
          <b>{d.open_leases}</b>
        </div>
        <div className="stat">
          冲突计数
          <b>{d.conflict_count}</b>
        </div>
        <div className="stat">
          占用中
          <b>{d.live_sessions}</b>
        </div>
        <div className="stat">
          待开窗
          <b>{d.draft_sessions}</b>
        </div>
      </div>
      <div className={`msg${aligned ? "" : " err"}`}>
        冲突计数直读 conflicts 表：计数 {d.conflict_count}｜列表可见 {d.conflict_rows_visible}
        {wm.present
          ? `｜水位 ${wm.stored}` +
            (wm.healed
              ? `（读取时与表不符，已按表回写，原值 ${wm.healed_from ?? "—"}）`
              : wm.stale
                ? "（与表不符，计数以表为准）"
                : "（与表一致）")
          : "｜无水位行（计数只认事件表）"}
        {aligned ? "（自洽）" : "（读数撕裂，禁止报绿）"}
      </div>
    </section>
  );
}
