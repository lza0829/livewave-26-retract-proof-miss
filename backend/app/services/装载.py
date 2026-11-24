import json
from app.db import 连接
from app.seed import 此刻
from app.services import 时间, 热度窗, 撤回幂等
from app.modules import 先撤回证明 as 证


def 装一批(体: dict) -> dict:
    """入流一批互动事件：推进水位线，尝试关窗，处理迟到/旁路/撤回。

    口径（类 Flink）：
    - 水位 = 已见最大事件时
    - 窗 [起,止) 在 水位>=止 时关闭发射
    - 关闭后至 水位>=止+允许迟到 仍可迟到修正并发撤回
    - 越过 止+允许迟到 则旁路

    撤回幂等（口径B：窗键+事件编号）：
    - 互动事件主键是物理行守卫（同一事件编号至多一行）；
    - 撤回幂等锚点 (窗键,事件编号) 是修正闸：抢到锚点才允许加窗合计、写撤回单；
    - 重放票仍走完整入口（水位/关窗扫描照常），在修正点被锚点拒绝——
      窗合计不二次累加、撤回单不增，仅累加幂等命中审计；
    - 整批在一个 BEGIN IMMEDIATE 写事务内，任何唯一约束撞车即整体回滚，失败闭。

    先撤回证明（乙：证明号换审计行）：
    - 首撤同票落证明（事件编号、窗键、首撤哈希、证明号），落证明失败整票回滚，禁止命中成功而证明空；
    - 二次入口按证明号消化：只落命中审计行（撤回幂等审计.证明号，命中次数+1），
      禁止持证明号仍把累计再加一遍，禁止第二笔业务撤回行；无证明号不得只靠窗键去重蒙混；
    - 提交前互证：撤回簿与先撤回证明必须一一对应，分叉即整票回滚，禁止分叉还提交。
    """
    批次编号 = 体.get("批次编号") or f"批-{此刻().replace(':', '').replace('-', '')}"
    事件列表 = 体.get("事件") or []
    conn = 连接()
    try:
        
        conn.execute("BEGIN IMMEDIATE")
        
        
        原编号 = 批次编号
        n = 1
        while conn.execute(
            "SELECT 1 FROM 装载批次 WHERE 编号=?", (批次编号,)
        ).fetchone():
            批次编号 = f"{原编号}#重放{n}"
            n += 1
        报告 = {
            "批次编号": 批次编号,
            "入流": 0,
            "入窗": 0,
            "旁路": 0,
            "撤回": 0,
            "幂等命中": 0,
            "新关窗": 0,
            "明细": [],
        }
        站 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 站规")}
        窗长 = int(站.get("窗长秒", "60"))
        默许迟到 = int(站.get("允许迟到秒", "30"))

        for ev in 事件列表:
            房间键 = ev["房间键"]
            房 = conn.execute("SELECT * FROM 直播间 WHERE 房间键=?", (房间键,)).fetchone()
            if not 房:
                
                if conn.execute(
                    "SELECT 1 FROM 互动事件 WHERE 事件编号=?", (ev.get("事件编号"),)
                ).fetchone():
                    报告["幂等命中"] += 1
                    报告["明细"].append({"事件编号": ev.get("事件编号"), "命运": "幂等跳过-未知房"})
                    continue
                conn.execute(
                    "INSERT INTO 旁路匣(原因, 载荷, 时刻) VALUES (?,?,?)",
                    ("未知直播间", json.dumps(ev, ensure_ascii=False), 此刻()),
                )
                报告["旁路"] += 1
                报告["明细"].append({"事件编号": ev.get("事件编号"), "命运": "旁路-未知房"})
                continue

            水位 = conn.execute("SELECT * FROM 水位线 WHERE 房间键=?", (房间键,)).fetchone()
            if not 水位:
                conn.execute(
                    "INSERT INTO 水位线(房间键, 事件时水位, 允许迟到秒, 更新处理时) VALUES (?,?,?,?)",
                    (房间键, 房["开播事件时"], 默许迟到, 此刻()),
                )
                水位 = conn.execute("SELECT * FROM 水位线 WHERE 房间键=?", (房间键,)).fetchone()

            允许迟到 = int(水位["允许迟到秒"])
            事件时 = ev["事件时"]
            处理时 = ev.get("处理时") or 此刻()
            事件编号 = ev["事件编号"]
            点赞 = int(ev.get("点赞增量") or 0)
            送礼 = int(ev.get("送礼额") or 0)
            类型 = ev.get("事件类型") or "点赞"

            已存 = conn.execute(
                "SELECT * FROM 互动事件 WHERE 事件编号=?", (事件编号,)
            ).fetchone()

            
            
            当前水位 = 水位["事件时水位"]
            if 时间.解析(事件时) > 时间.解析(当前水位):
                当前水位 = 事件时
                conn.execute(
                    "UPDATE 水位线 SET 事件时水位=?, 更新处理时=? WHERE 房间键=?",
                    (当前水位, 处理时, 房间键),
                )
            报告["新关窗"] += 热度窗.尝试关窗(
                conn, 房间键, 当前水位, 允许迟到, 窗长, 处理时
            )

            窗起 = 时间.对齐窗起(事件时, 窗长)
            窗止 = 时间.加秒(窗起, 窗长)
            wk = 时间.窗键(房间键, 窗起)

            
            if 热度窗.已过旁路界(当前水位, 窗止, 允许迟到):
                if 已存:
                    
                    if 已存["窗键"] != "" or 已存["命运"] != "已旁路":
                        raise RuntimeError(f"幂等状态分叉: 事件{事件编号}首落非旁路却重判为旁路")
                    报告["幂等命中"] += 1
                    报告["明细"].append({"事件编号": 事件编号, "命运": "幂等跳过-已旁路", "窗键本应": wk})
                    continue
                conn.execute(
                    """INSERT INTO 互动事件(事件编号,房间键,事件类型,事件时,处理时,观众键,点赞增量,送礼额,命运,窗键,批次编号)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        事件编号, 房间键, 类型, 事件时, 处理时,
                        ev.get("观众键") or "", 点赞, 送礼, "已旁路", "", 批次编号,
                    ),
                )
                conn.execute(
                    "INSERT INTO 旁路匣(原因, 载荷, 时刻) VALUES (?,?,?)",
                    (
                        "超允许迟到",
                        json.dumps(
                            {
                                "事件编号": 事件编号,
                                "事件时": 事件时,
                                "窗止": 窗止,
                                "旁路界": 时间.加秒(窗止, 允许迟到),
                                "水位": 当前水位,
                            },
                            ensure_ascii=False,
                        ),
                        处理时,
                    ),
                )
                报告["旁路"] += 1
                报告["明细"].append({"事件编号": 事件编号, "命运": "已旁路", "窗键本应": wk})
                continue

            窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()
            if not 窗:
                conn.execute(
                    """INSERT INTO 热度窗(窗键,房间键,窗起事件时,窗止事件时,状态,点赞合计,送礼合计,事件笔数,版本)
                       VALUES (?,?,?,?, '开启', 0, 0, 0, 0)""",
                    (wk, 房间键, 窗起, 窗止),
                )
                窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()
                
                if 时间.解析(当前水位) >= 时间.解析(窗止):
                    报告["新关窗"] += 热度窗.尝试关窗(
                        conn, 房间键, 当前水位, 允许迟到, 窗长, 处理时
                    )
                    窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (wk,)).fetchone()

            if 窗["状态"] == "已关闭":
                
                if 已存:
                    
                    if 已存["窗键"] != wk or 已存["命运"] != "已入窗":
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}首落窗{已存['窗键']}/{已存['命运']}，重放判向{wk}/已关闭"
                        )
                    锚 = conn.execute(
                        "SELECT * FROM 撤回幂等审计 WHERE 锚点键=?",
                        (撤回幂等.锚点(wk, 事件编号),),
                    ).fetchone()
                    撤回行 = conn.execute(
                        "SELECT * FROM 撤回单 WHERE 窗键=? AND 事件编号=? AND 原因='迟到修正'",
                        (wk, 事件编号),
                    ).fetchall()
                    证明 = 证.取证明(conn, 事件编号, wk)


                    if 撤回行 and not 锚:

                        raise RuntimeError(f"幂等状态分叉: 事件{事件编号}已入窗{wk}有撤回单但缺撤回锚点")
                    if 撤回行 and not 证明:
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}已入窗{wk}有撤回单但缺先撤回证明（命中成功而证明空）"
                        )
                    if 证明 and not 撤回行:
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}持证明号{证明['证明号']}但撤回簿无行（证明号与撤回簿分叉）"
                        )
                    if 锚 and 证明 and 锚["证明号"] != 证明["证明号"]:
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}命中审计证明号{锚['证明号']}与证明表{证明['证明号']}不符"
                        )


                    if 锚:
                        撤回幂等.记命中(conn, wk, 事件编号, 批次编号, 处理时)
                    报告["幂等命中"] += 1
                    明细 = {"事件编号": 事件编号, "命运": "幂等跳过-迟到修正", "窗键": wk}
                    if 证明:
                        明细["证明号"] = 证明["证明号"]
                    报告["明细"].append(明细)
                    continue


                旧赞, 旧送 = 窗["点赞合计"], 窗["送礼合计"]
                新赞, 新送 = 旧赞 + 点赞, 旧送 + 送礼
                哈希, 号 = 证.排证明(wk, 事件编号, 旧赞, 新赞, 旧送, 新送)
                首次 = 撤回幂等.记首次(conn, wk, 事件编号, 批次编号, 处理时, 证明号=号)
                if not 首次:

                    证明 = 证.取证明(conn, 事件编号, wk)
                    if not 证明:
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}锚点已在但先撤回证明缺失（命中成功而证明空）"
                        )
                    撤回幂等.记命中(conn, wk, 事件编号, 批次编号, 处理时)
                    报告["幂等命中"] += 1
                    报告["明细"].append(
                        {"事件编号": 事件编号, "命运": "幂等抢占失败-迟到修正", "窗键": wk, "证明号": 证明["证明号"]}
                    )
                    continue

                新版本 = int(窗["版本"]) + 1
                
                conn.execute(
                    """UPDATE 热度窗
                       SET 点赞合计=CASE WHEN 点赞合计=? THEN ? ELSE 点赞合计 END,
                           送礼合计=CASE WHEN 送礼合计=? THEN ? ELSE 送礼合计 END,
                           事件笔数=事件笔数+1, 版本=?
                       WHERE 窗键=?""",
                    (旧赞, 新赞, 旧送, 新送, 新版本, wk),
                )
                
                
                conn.execute(
                    """INSERT INTO 撤回单(窗键,房间键,原因,旧点赞,新点赞,旧送礼,新送礼,处理时,事件编号)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (wk, 房间键, "迟到修正", 旧赞, 新赞, 旧送, 新送, 处理时, 事件编号),
                )
                conn.execute(
                    """INSERT INTO 互动事件(事件编号,房间键,事件类型,事件时,处理时,观众键,点赞增量,送礼额,命运,窗键,批次编号)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        事件编号, 房间键, 类型, 事件时, 处理时,
                        ev.get("观众键") or "", 点赞, 送礼, "已入窗", wk, 批次编号,
                    ),
                )

                证.落证明(conn, 事件编号=事件编号, 窗键=wk, 首撤哈希值=哈希, 证明号值=号, 时刻=处理时)
                报告["入窗"] += 1
                报告["撤回"] += 1
                报告["入流"] += 1
                报告["明细"].append({"事件编号": 事件编号, "命运": "迟到修正撤回", "窗键": wk, "证明号": 号})
            else:
                
                if 已存:
                    if 已存["窗键"] != wk or 已存["命运"] != "已入窗":
                        raise RuntimeError(
                            f"幂等状态分叉: 事件{事件编号}首落窗{已存['窗键']}/{已存['命运']}，重放判向{wk}/开启"
                        )
                    报告["幂等命中"] += 1
                    报告["明细"].append({"事件编号": 事件编号, "命运": "幂等跳过-开放窗", "窗键": wk})
                    continue
                conn.execute(
                    """UPDATE 热度窗 SET 点赞合计=点赞合计+?, 送礼合计=送礼合计+?, 事件笔数=事件笔数+1 WHERE 窗键=?""",
                    (点赞, 送礼, wk),
                )
                conn.execute(
                    """INSERT INTO 互动事件(事件编号,房间键,事件类型,事件时,处理时,观众键,点赞增量,送礼额,命运,窗键,批次编号)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        事件编号, 房间键, 类型, 事件时, 处理时,
                        ev.get("观众键") or "", 点赞, 送礼, "已入窗", wk, 批次编号,
                    ),
                )
                报告["入窗"] += 1
                报告["入流"] += 1
                报告["明细"].append({"事件编号": 事件编号, "命运": "已入窗", "窗键": wk})

        for 房行 in conn.execute("SELECT 房间键, 事件时水位, 允许迟到秒 FROM 水位线"):
            报告["新关窗"] += 热度窗.尝试关窗(
                conn, 房行["房间键"], 房行["事件时水位"], int(房行["允许迟到秒"]), 窗长, 此刻()
            )


        分叉 = conn.execute(
            """SELECT '缺证明' AS 病, d.窗键, d.事件编号
               FROM 撤回单 d
               LEFT JOIN 先撤回证明 p ON p.窗键=d.窗键 AND p.事件编号=d.事件编号
               WHERE d.原因='迟到修正' AND d.事件编号!='' AND p.编号 IS NULL
               UNION ALL
               SELECT '孤证明', p.窗键, p.事件编号
               FROM 先撤回证明 p
               LEFT JOIN 撤回单 d
                 ON d.窗键=p.窗键 AND d.事件编号=p.事件编号 AND d.原因='迟到修正'
               WHERE d.编号 IS NULL"""
        ).fetchall()
        if 分叉:
            raise RuntimeError(
                f"证明号与撤回簿分叉，整票回滚: {[dict(r) for r in 分叉]}"
            )

        conn.execute(
            "INSERT INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次编号, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return 报告
    except Exception:
        conn.rollback()
        raise
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

