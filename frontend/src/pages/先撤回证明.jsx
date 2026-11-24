import React, { useEffect, useState } from "react";
import { 取 } from "../api.js";

export default function 先撤回证明() {
  const [果, 设果] = useState(null);
  const [err, setErr] = useState("");
  const [事件编号, 设事件编号] = useState("");
  const reload = (ev) => {
    const q = (ev ?? 事件编号).trim();
    取("/核证/先撤回证明" + (q ? `?事件编号=${encodeURIComponent(q)}` : ""))
      .then((d) => { 设果(d); setErr(""); })
      .catch((e) => { setErr(String(e.message || e)); 设果(null); });
  };
  useEffect(() => { reload(""); }, []);
  return (
    <div>
      <h1 className="题">先撤回证明核证</h1>
      <p className="述">
        乙：证明号换审计行。首撤同票落证明（事件编号、窗键、首撤哈希、证明号）；二次入口只落命中审计、
        不动累计、不写第二笔业务撤回。此处四方对账：证明 ↔ 撤回簿 ↔ 命中审计 ↔ 热度窗/事件。纯读，无副作用。
      </p>
      <div className="行">
        <input
          placeholder="事件编号（可空，空则核证全表）"
          value={事件编号}
          onChange={(e) => 设事件编号(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <button className="水" onClick={() => reload()}>核证</button>
        <button className="次" onClick={() => { 设事件编号(""); reload(""); }}>全表</button>
      </div>
      {err && <pre className="码">{err}</pre>}
      {果 && (
        <div className="证板">
          <div className={`证旗 ${果.valid ? "通" : "败"}`}>
            <div className="标">{果.valid ? "对齐" : "撕裂"}</div>
            <div className="辅">口径：{果.口径} · 证明 {果.证明数} 条</div>
          </div>
          {!果.valid && <div className="报箱 码">{JSON.stringify(果.问题, null, 2)}</div>}
        </div>
      )}
      {果 && 果.证明 && 果.证明.length > 0 && (
        <div className="段">
          <div className="段题">证明链</div>
          <table>
            <thead>
              <tr>
                <th>证明号</th><th>事件编号</th><th>窗键</th><th>首撤哈希</th>
                <th>撤回行</th><th>幂等命中</th><th>哈希核证</th><th>窗合计（赞/送/笔）</th><th>时刻</th>
              </tr>
            </thead>
            <tbody>
              {果.证明.map((p) => (
                <tr key={p.编号}>
                  <td className="码">{p.证明号}</td>
                  <td>{p.事件编号}</td>
                  <td className="码">{p.窗键}</td>
                  <td className="码">{p.首撤哈希}</td>
                  <td>
                    <span className={`徽章 ${p.撤回行数 === 1 ? "通" : "败"}`}>{p.撤回行数}</span>
                  </td>
                  <td>{p.幂等命中}</td>
                  <td>
                    <span className={`徽章 ${p.哈希核证 === "符" ? "通" : "败"}`}>{p.哈希核证}</span>
                  </td>
                  <td className="码">
                    {p.窗合计 ? `${p.窗合计.点赞} / ${p.窗合计.送礼} / ${p.窗合计.笔数}` : "—"}
                  </td>
                  <td className="码">{p.时刻}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {果 && (!果.证明 || 果.证明.length === 0) && (
        <div className="空态">暂无证明。先推「迟到修正样例」让首撤落证明。</div>
      )}
    </div>
  );
}
