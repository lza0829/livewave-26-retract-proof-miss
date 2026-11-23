from app.db import 连接
from app.seed import 此刻


def 跑贯验() -> dict:
    """窗输出与撤回互证：已关闭窗的点赞/送礼须等于窗内事件合计；撤回后版本与末次撤回一致。"""
    conn = 连接()
    try:
        问题 = []
        事件数 = conn.execute("SELECT COUNT(*) FROM 互动事件").fetchone()[0]
        已关闭 = conn.execute("SELECT * FROM 热度窗 WHERE 状态='已关闭'").fetchall()
        撤回数 = conn.execute("SELECT COUNT(*) FROM 撤回单").fetchone()[0]
        旁路数 = conn.execute("SELECT COUNT(*) FROM 旁路匣").fetchone()[0]

        for 窗 in 已关闭:
            agg = conn.execute(
                """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n
                   FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
                (窗["窗键"],),
            ).fetchone()
            if agg["赞"] != 窗["点赞合计"] or agg["送"] != 窗["送礼合计"]:
                问题.append(
                    f"窗{窗['窗键']}合计不符: 窗表赞{窗['点赞合计']}/送{窗['送礼合计']} vs 事件赞{agg['赞']}/送{agg['送']}"
                )
            if agg["n"] != 窗["事件笔数"]:
                问题.append(f"窗{窗['窗键']}笔数不符: 窗表{窗['事件笔数']} vs 事件{agg['n']}")

            末撤 = conn.execute(
                "SELECT * FROM 撤回单 WHERE 窗键=? ORDER BY 编号 DESC LIMIT 1",
                (窗["窗键"],),
            ).fetchone()
            if 末撤 and (末撤["新点赞"] != 窗["点赞合计"] or 末撤["新送礼"] != 窗["送礼合计"]):
                问题.append(f"窗{窗['窗键']}末次撤回与窗表不一致")

        
        脏旁路 = conn.execute(
            "SELECT COUNT(*) FROM 互动事件 WHERE 命运='已旁路' AND 窗键!=''"
        ).fetchone()[0]
        if 脏旁路:
            问题.append(f"旁路事件仍挂窗键:{脏旁路}")

        
        双撤回 = conn.execute(
            """SELECT 窗键, 事件编号, COUNT(*) AS c
               FROM 撤回单
               WHERE 原因='迟到修正' AND 事件编号!=''
               GROUP BY 窗键, 事件编号 HAVING c > 1"""
        ).fetchall()
        for r in 双撤回:
            问题.append(f"撤回锚点重复: 窗{r['窗键']}事件{r['事件编号']}有{r['c']}行撤回（应≤1）")

        
        无审计 = conn.execute(
            """SELECT d.窗键, d.事件编号
               FROM 撤回单 d
               LEFT JOIN 撤回幂等审计 a
                 ON a.锚点键 = d.窗键||'|'||d.事件编号
               WHERE d.原因='迟到修正' AND d.事件编号!='' AND a.锚点键 IS NULL"""
        ).fetchall()
        for r in 无审计:
            问题.append(f"撤回锚点缺审计: 窗{r['窗键']}事件{r['事件编号']}")

        
        撤回 = conn.execute(
            """SELECT d.编号, d.窗键, d.事件编号, d.新点赞-d.旧点赞 AS 增赞, d.新送礼-d.旧送礼 AS 增送
               FROM 撤回单 d WHERE d.原因='迟到修正' AND d.事件编号!=''"""
        ).fetchall()
        for d in 撤回:
            ev = conn.execute(
                "SELECT 点赞增量, 送礼额 FROM 互动事件 WHERE 事件编号=?",
                (d["事件编号"],),
            ).fetchone()
            if not ev or ev["点赞增量"] != d["增赞"] or ev["送礼额"] != d["增送"]:
                问题.append(f"撤回单编号{d['编号']}增量与触发事件{d['事件编号']}不符")

        通过 = 1 if not 问题 else 0
        说明 = "窗输出与事件/撤回互证通过" if 通过 else "；".join(问题)[:800]
        conn.execute(
            """INSERT INTO 窗贯验(时刻,事件数,已关闭窗数,撤回数,旁路数,是否通过,说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 事件数, len(已关闭), 撤回数, 旁路数, 通过, 说明),
        )
        conn.commit()
        return {
            "是否通过": bool(通过),
            "事件数": 事件数,
            "已关闭窗数": len(已关闭),
            "撤回数": 撤回数,
            "旁路数": 旁路数,
            "说明": 说明,
            "问题": 问题,
        }
    finally:
        conn.close()

def _alias_payload(payload: dict) -> dict:
    out = dict(payload or {})
    if "窗键" in out and "window_key" not in out:
        out["window_key"] = out.get("窗键")
    if "房间键" in out and "room_key" not in out:
        out["room_key"] = out.get("房间键")
    if "事件编号" in out and "event_id" not in out:
        out["event_id"] = out.get("事件编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows(rows: list) -> list:
    return [_alias_payload(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_26(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "livewave", "nn": "26"}
    if extra:
        data.update(extra)
    return data

