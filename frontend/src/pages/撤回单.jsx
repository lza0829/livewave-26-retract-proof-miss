import React, { useEffect, useState } from "react";
import { 取 } from "../api.js";

export default function 撤回单页() {
  const [行, 设] = useState([]);
  const [事件编号, 设事件编号] = useState("L1");
  const [窗键, 设窗键] = useState("");
  const [果, 设果] = useState(null);
  const [错, 设错] = useState("");
  const 刷 = () => 取("/撤回单").then(设);
  useEffect(() => { 刷(); }, []);

  const 查 = async () => {
    设错("");
    设果(null);
    const q = new URLSearchParams({ 事件编号 });
    if (窗键.trim()) q.set("窗键", 窗键.trim());
    try {
      设果(await 取(`/撤回幂等?${q.toString()}`));
    } catch (e) {
      设错(String(e));
    }
  };

  return (
    <div>
      <h1 className="题">④ 撤回单</h1>
      <p className="述">
        已关闭窗在「窗止 + 允许迟到」之前又进来事件时：先抢撤回幂等锚点（窗键+事件编号），
        锚点成功才改窗合计、记一笔撤回（旧值 → 新值）。原包重放只累加幂等命中，合计不二次累加、撤回不增行。
      </p>
      <div className="行"><button className="次" onClick={刷}>刷新撤回</button></div>

      <div className="段">
        <div className="段题">撤回幂等只读接口</div>
        <div className="行">
          <input
            placeholder="事件编号，如 L1"
            value={事件编号}
            onChange={(e) => 设事件编号(e.target.value)}
          />
          <input
            placeholder="窗键（可空，按事件时反推）"
            value={窗键}
            onChange={(e) => 设窗键(e.target.value)}
            style={{ minWidth: 260 }}
          />
          <button className="水" onClick={查}>查幂等</button>
        </div>
        {错 && <div className="空态">{错}</div>}
        {果 && (
          <table>
            <thead>
              <tr>
                <th>锚点口径</th><th>valid</th><th>是否二次</th><th>幂等命中</th>
                <th>事件行</th><th>撤回行</th><th>证明号</th><th>窗合计（赞/送/笔）</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="码">{果.锚点口径}</td>
                <td><span className={`徽章 ${果.valid ? "通" : "败"}`}>{果.valid ? "有效" : "撕裂"}</span></td>
                <td>
                  <span className={`徽章 ${果.是否二次 ? "败" : "通"}`}>
                    {果.是否二次 ? "二次重放" : "首次"}
                  </span>
                </td>
                <td>{果.幂等命中}</td>
                <td>{果.事件行数}</td>
                <td>{果.撤回行数}</td>
                <td className="码">{果.证明号 || "—"}</td>
                <td className="码">
                  {果.合计 ? `${果.合计.点赞} / ${果.合计.送礼} / ${果.合计.笔数}` : "—"}
                </td>
              </tr>
            </tbody>
          </table>
        )}
        {果 && !果.valid && (
          <div className="报箱 码">{JSON.stringify(果.问题, null, 2)}</div>
        )}
      </div>

      <div className="段">
        <div className="段题">撤回链</div>
        {!行.length ? (
          <div className="空态">暂无撤回。准时关窗后再推「迟到修正样例」。</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th><th>窗键</th><th>原因</th><th>点赞旧→新</th><th>送礼旧→新</th>
                <th>触发事件</th><th>证明号</th><th>幂等命中</th><th>处理时</th>
              </tr>
            </thead>
            <tbody>
              {行.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td><td className="码">{r.窗键}</td><td>{r.原因}</td>
                  <td>{r.旧点赞} → {r.新点赞}</td><td>{r.旧送礼} → {r.新送礼}</td>
                  <td>{r.事件编号}</td>
                  <td className="码">{r.证明号 || "—"}</td>
                  <td>
                    <span className={`徽章 ${r.幂等命中 > 1 ? "败" : "通"}`}>{r.幂等命中}</span>
                  </td>
                  <td className="码">{r.处理时}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
