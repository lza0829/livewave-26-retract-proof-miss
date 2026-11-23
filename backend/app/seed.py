from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 先撤回证明 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  事件编号 TEXT NOT NULL,
  窗键 TEXT NOT NULL DEFAULT '',
  首撤哈希 TEXT NOT NULL DEFAULT '',
  证明号 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 直播间 (
  房间键 TEXT PRIMARY KEY,
  标题 TEXT NOT NULL,
  主播 TEXT NOT NULL,
  开播事件时 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '直播中'
);
CREATE TABLE IF NOT EXISTS 互动事件 (
  事件编号 TEXT PRIMARY KEY,
  房间键 TEXT NOT NULL REFERENCES 直播间(房间键),
  事件类型 TEXT NOT NULL,
  事件时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  观众键 TEXT NOT NULL DEFAULT '',
  点赞增量 INTEGER NOT NULL DEFAULT 0,
  送礼额 INTEGER NOT NULL DEFAULT 0,
  命运 TEXT NOT NULL DEFAULT '已入流',
  窗键 TEXT NOT NULL DEFAULT '',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 水位线 (
  房间键 TEXT PRIMARY KEY REFERENCES 直播间(房间键),
  事件时水位 TEXT NOT NULL,
  允许迟到秒 INTEGER NOT NULL DEFAULT 30,
  更新处理时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 热度窗 (
  窗键 TEXT PRIMARY KEY,
  房间键 TEXT NOT NULL REFERENCES 直播间(房间键),
  窗起事件时 TEXT NOT NULL,
  窗止事件时 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '开启',
  点赞合计 INTEGER NOT NULL DEFAULT 0,
  送礼合计 INTEGER NOT NULL DEFAULT 0,
  事件笔数 INTEGER NOT NULL DEFAULT 0,
  关闭处理时 TEXT NOT NULL DEFAULT '',
  版本 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS 撤回单 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  窗键 TEXT NOT NULL,
  房间键 TEXT NOT NULL,
  原因 TEXT NOT NULL,
  旧点赞 INTEGER NOT NULL,
  新点赞 INTEGER NOT NULL,
  旧送礼 INTEGER NOT NULL,
  新送礼 INTEGER NOT NULL,
  处理时 TEXT NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT ''
);
-- 撤回幂等锚点（口径B）：同一事件编号对同一热度窗的迟到修正，全局至多一行撤回。
-- 部分唯一索引：只约束迟到修正（带事件编号）的撤回，不绑死未来其它原因的撤回单。
CREATE TABLE IF NOT EXISTS 撤回幂等审计 (
  锚点键 TEXT PRIMARY KEY,
  窗键 TEXT NOT NULL,
  事件编号 TEXT NOT NULL,
  首次批次编号 TEXT NOT NULL DEFAULT '',
  首次处理时 TEXT NOT NULL,
  命中次数 INTEGER NOT NULL DEFAULT 1,
  末次处理时 TEXT NOT NULL,
  末次批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 旁路匣 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 窗贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  事件数 INTEGER NOT NULL,
  已关闭窗数 INTEGER NOT NULL,
  撤回数 INTEGER NOT NULL,
  旁路数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
"""


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _幂等迁移(conn) -> None:
    """对旧库（撤回幂等键空桩时期可能已双计）做一次性自愈，再上硬约束。

    顺序必须是：先清重复撤回 → 按事件表重算窗合计/笔数 → 回填审计 → 最后建唯一索引。
    约束最后建，保证建索引时库里已不存在违反锚点的行（否则 CREATE INDEX 直接失败闭）。
    """
    
    重复 = conn.execute(
        """SELECT 窗键, 事件编号, MIN(编号) AS 留
           FROM 撤回单
           WHERE 原因='迟到修正' AND 事件编号!=''
           GROUP BY 窗键, 事件编号
           HAVING COUNT(*) > 1"""
    ).fetchall()
    for r in 重复:
        conn.execute(
            "DELETE FROM 撤回单 WHERE 窗键=? AND 事件编号=? AND 编号>?",
            (r["窗键"], r["事件编号"], r["留"]),
        )

    
    for 窗 in conn.execute("SELECT 窗键 FROM 热度窗").fetchall():
        agg = conn.execute(
            """SELECT COALESCE(SUM(点赞增量),0) AS 赞, COALESCE(SUM(送礼额),0) AS 送, COUNT(*) AS n
               FROM 互动事件 WHERE 窗键=? AND 命运='已入窗'""",
            (窗["窗键"],),
        ).fetchone()
        conn.execute(
            "UPDATE 热度窗 SET 点赞合计=?, 送礼合计=?, 事件笔数=? WHERE 窗键=?",
            (agg["赞"], agg["送"], agg["n"], 窗["窗键"]),
        )

    
    conn.execute(
        """INSERT OR IGNORE INTO 撤回幂等审计(锚点键, 窗键, 事件编号, 首次批次编号, 首次处理时, 命中次数, 末次处理时, 末次批次编号)
           SELECT 窗键||'|'||事件编号, 窗键, 事件编号, '', MIN(处理时), 1, MAX(处理时), ''
           FROM 撤回单
           WHERE 原因='迟到修正' AND 事件编号!=''
           GROUP BY 窗键, 事件编号"""
    )

    
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS 撤回单_幂等锚点
           ON 撤回单(窗键, 事件编号)
           WHERE 原因='迟到修正' AND 事件编号!=''"""
    )


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _幂等迁移(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 站规").fetchone()["c"] == 0:
        for k, v in {
            "站名": "播浪",
            "窗长秒": "60",
            "允许迟到秒": "30",
            "水位推进策略": "事件时最大值",
            "关窗条件": "水位>=窗止即关闭发射",
            "旁路条件": "水位>=窗止+允许迟到",
            "骨架": "互动事件入流→事件时间水位线推进→热度窗关闭发射→迟到修正撤回→窗输出与撤回互证",
        }.items():
            conn.execute("INSERT INTO 站规(键, 值) VALUES (?, ?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 直播间").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO 直播间(房间键, 标题, 主播, 开播事件时, 状态) VALUES (?,?,?,?,?)",
            ("房A", "晚间热聊", "主播小浪", "2026-11-22T12:00:00Z", "直播中"),
        )
        conn.execute(
            "INSERT INTO 水位线(房间键, 事件时水位, 允许迟到秒, 更新处理时) VALUES (?,?,?,?)",
            ("房A", "2026-11-22T12:00:00Z", 30, 此刻()),
        )
    conn.commit()
    conn.close()
