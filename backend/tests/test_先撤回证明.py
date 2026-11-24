"""先撤回证明（乙：证明号换审计行）夹具测试。

题面夹具：
1. 首撤成功后再进同一迟到写——累计不变；乙必须落命中审计且业务撤回仍一行，证明仍一行；
2. 二次入口与关闭扫描抢锁——不得半截再加累计；
3. 核证只读接口 /api/核证/先撤回证明 的字段与 valid，且纯读无副作用；
4. 硬约束：命中成功而证明空（人为删证明）重放必须报错回滚；
   证明号与撤回簿行数分叉时，任何后续装载票都不得提交。

运行：python -m unittest backend.tests.test_先撤回证明 -v
"""
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

根 = Path(__file__).resolve().parents[2]
夹具目录 = 根 / "fixtures"
窗键 = "房A|2026-11-22T12:00:00Z"


class 证明基类(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["数据目录"] = self.tmp.name
        os.environ["夹具目录"] = str(夹具目录)
        from app import seed
        from app.db import 连接
        self.连接 = 连接
        seed.初始化库()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("数据目录", None)
        os.environ.pop("夹具目录", None)

    def 读夹具(self, 名):
        return json.loads((夹具目录 / f"{名}.json").read_text(encoding="utf-8"))

    def 装(self, 体):
        from app.services import 装载
        return 装载.装一批(体)

    def 快照(self):
        conn = self.连接()
        try:
            窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()
            return {
                "点赞": 窗["点赞合计"], "送礼": 窗["送礼合计"], "笔数": 窗["事件笔数"],
                "事件行": conn.execute("SELECT COUNT(*) c FROM 互动事件 WHERE 事件编号='L1'").fetchone()["c"],
                "撤回行": conn.execute(
                    "SELECT COUNT(*) c FROM 撤回单 WHERE 窗键=? AND 事件编号='L1' AND 原因='迟到修正'", (窗键,)
                ).fetchone()["c"],
                "证明行": conn.execute(
                    "SELECT COUNT(*) c FROM 先撤回证明 WHERE 窗键=? AND 事件编号='L1'", (窗键,)
                ).fetchone()["c"],
                "命中": (lambda a: int(a["命中次数"]) if a else 0)(
                    conn.execute("SELECT * FROM 撤回幂等审计 WHERE 锚点键=?", (f"{窗键}|L1",)).fetchone()
                ),
            }
        finally:
            conn.close()

    def 证明(self):
        conn = self.连接()
        try:
            return conn.execute(
                "SELECT * FROM 先撤回证明 WHERE 窗键=? AND 事件编号='L1'", (窗键,)
            ).fetchone()
        finally:
            conn.close()

    def 核证(self, 事件编号=None):
        from app.modules import 先撤回证明 as 证
        conn = self.连接()
        try:
            return 证.查证明(conn, 事件编号)
        finally:
            conn.close()


class 首撤与重放(证明基类):
    def test_首撤同票落证明_四件齐全(self):
        self.装(self.读夹具("准时热度样例"))
        r = self.装(self.读夹具("迟到修正样例"))
        self.assertEqual(r["撤回"], 1)

        p = self.证明()
        self.assertIsNotNone(p, "首撤成功必须同票落先撤回证明（禁止命中成功而证明空）")
        self.assertEqual(p["事件编号"], "L1")
        self.assertEqual(p["窗键"], 窗键)
        self.assertTrue(p["首撤哈希"])
        self.assertTrue(p["证明号"].startswith("证-"))
        self.assertTrue(p["时刻"])

        from app.modules import 先撤回证明 as 证
        conn = self.连接()
        try:
            d = conn.execute(
                "SELECT * FROM 撤回单 WHERE 窗键=? AND 事件编号='L1' AND 原因='迟到修正'", (窗键,)
            ).fetchone()
            望哈希, 望号 = 证.排证明(d["窗键"], d["事件编号"], d["旧点赞"], d["新点赞"], d["旧送礼"], d["新送礼"])
            审计号 = conn.execute(
                "SELECT 证明号 FROM 撤回幂等审计 WHERE 锚点键=?", (f"{窗键}|L1",)
            ).fetchone()["证明号"]
        finally:
            conn.close()
        self.assertEqual((p["首撤哈希"], p["证明号"]), (望哈希, 望号), "证明须可按撤回簿重算")
        self.assertEqual(审计号, p["证明号"], "命中审计行唯一键须含证明号")

        明细 = [m for m in r["明细"] if m["事件编号"] == "L1"][0]
        self.assertEqual(明细["证明号"], p["证明号"])

    def test_再进同一迟到写_累计不变_乙落命中审计(self):
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        前 = self.快照()
        self.assertEqual((前["点赞"], 前["送礼"], 前["笔数"]), (7, 50, 3))
        self.assertEqual((前["事件行"], 前["撤回行"], 前["证明行"], 前["命中"]), (1, 1, 1, 1))

        r = self.装(self.读夹具("迟到修正重放样例"))
        self.assertEqual(r["幂等命中"], 1)
        self.assertEqual(r["撤回"], 0)
        后 = self.快照()

        self.assertEqual((后["点赞"], 后["送礼"], 后["笔数"]), (前["点赞"], 前["送礼"], 前["笔数"]),
                         "持证明号不得再把累计加一遍")
        self.assertEqual(后["撤回行"], 1, "禁止第二笔业务撤回行")
        self.assertEqual(后["证明行"], 1, "证明仍一行")
        self.assertEqual(后["命中"], 2, "乙：必须落命中审计（命中次数+1）")
        明细 = [m for m in r["明细"] if m["事件编号"] == "L1"][0]
        self.assertEqual(明细["证明号"], self.证明()["证明号"], "二次入口须按证明号消化")

        果 = self.核证("L1")
        self.assertTrue(果["valid"], 果["问题"])
        self.assertEqual(果["证明数"], 1)
        self.assertEqual(果["证明"][0]["幂等命中"], 2)
        self.assertEqual(果["证明"][0]["撤回行数"], 1)
        self.assertEqual(果["证明"][0]["哈希核证"], "符")

    def test_核证只读接口_HTTP_纯读无副作用(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            c.post("/api/装载/夹具", params={"名": "准时热度样例"})
            c.post("/api/装载/夹具", params={"名": "迟到修正样例"})
            c.post("/api/装载/夹具", params={"名": "迟到修正重放样例"})

            前 = self.快照()
            r = c.get("/api/核证/先撤回证明", params={"事件编号": "L1"})
            self.assertEqual(r.status_code, 200)
            b = r.json()
            self.assertEqual(b["口径"], "证明号换审计行")
            self.assertTrue(b["valid"], b["问题"])
            self.assertEqual(b["证明数"], 1)
            self.assertEqual(b["证明"][0]["事件编号"], "L1")
            self.assertEqual(b["证明"][0]["窗键"], 窗键)
            self.assertEqual(b["证明"][0]["幂等命中"], 2)
            self.assertEqual(b["证明"][0]["窗合计"], {"点赞": 7, "送礼": 50, "笔数": 3})
            self.assertEqual(self.快照(), 前, "核证只读接口不得产生副作用")

            r全 = c.get("/api/核证/先撤回证明")
            self.assertEqual(r全.status_code, 200)
            self.assertTrue(r全.json()["valid"])
            self.assertGreaterEqual(r全.json()["证明数"], 1)

            self.assertEqual(c.get("/api/核证/先撤回证明", params={"事件编号": "不存在"}).status_code, 404)

            r幂 = c.get("/api/撤回幂等", params={"事件编号": "L1"})
            self.assertTrue(r幂.json()["证明号"].startswith("证-"))


class 抢锁夹具(证明基类):
    def test_二次入口与关闭扫描抢锁_不半截(self):
        """N 线程并发推同一迟到事件（首推+二次入口交错关窗扫描）：
        不得半截再加累计——事件行/撤回行/证明行恒 1，合计只加一次，命中=成功票数。"""
        准时 = self.读夹具("准时热度样例")
        迟到 = self.读夹具("迟到修正样例")
        self.装({"批次编号": "setup-1", "事件": [准时["事件"][0]]})
        self.装({"批次编号": "setup-2", "事件": [准时["事件"][2]]})
        关前 = self.快照()

        屏障 = threading.Barrier(4)
        错误 = []

        def 工(i):
            try:
                屏障.wait()
                self.装(json.loads(json.dumps(迟到, ensure_ascii=False))
                        | {"批次编号": f"并发-{i}-{uuid.uuid4().hex[:8]}"})
            except Exception as e:
                错误.append(repr(e))

        线程 = [threading.Thread(target=工, args=(i,)) for i in range(4)]
        for t in 线程:
            t.start()
        for t in 线程:
            t.join()

        后 = self.快照()
        self.assertEqual(后["事件行"], 1)
        self.assertEqual(后["撤回行"], 1)
        self.assertEqual(后["证明行"], 1)
        self.assertEqual(后["点赞"], 关前["点赞"] + 4, "累计只许加一次，不得半截双加")
        self.assertEqual(后["笔数"], 关前["笔数"] + 1)
        self.assertEqual(后["命中"], 4 - sum(1 for e in 错误 if "locked" in e.lower()))
        self.assertGreaterEqual(后["命中"], 1)

        果 = self.核证("L1")
        self.assertTrue(果["valid"], 果["问题"])

        from app.services import 贯验
        self.assertTrue(贯验.跑贯验()["是否通过"])


class 硬约束(证明基类):
    def test_命中成功而证明空_重放整票回滚(self):
        """人为删证明（模拟空桩时代半截脏账）：重放必须抛错且整票回滚，合计/撤回/命中不动。"""
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        前 = self.快照()

        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 先撤回证明 WHERE 窗键=? AND 事件编号='L1'", (窗键,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.快照()["证明行"], 0)

        with self.assertRaises(RuntimeError):
            self.装(self.读夹具("迟到修正重放样例"))

        后 = self.快照()
        self.assertEqual((后["点赞"], 后["送礼"], 后["笔数"]), (前["点赞"], 前["送礼"], 前["笔数"]))
        self.assertEqual((后["事件行"], 后["撤回行"], 后["命中"]), (1, 1, 1))

        果 = self.核证("L1")
        self.assertFalse(果["valid"], "缺证明必须被核证判撕裂")
        self.assertTrue(any("证明空" in q for q in 果["问题"]))

    def test_证明号与撤回簿分叉_后续票不得提交(self):
        """缺证明的脏账未修复时，任何后续装载票提交前互证必须整票回滚。"""
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 先撤回证明 WHERE 窗键=? AND 事件编号='L1'", (窗键,))
            conn.commit()
        finally:
            conn.close()

        新票 = {
            "批次编号": "分叉后新票",
            "事件": [{
                "事件编号": "E9", "房间键": "房A", "事件类型": "点赞",
                "事件时": "2026-11-22T12:01:30Z", "处理时": "2026-11-22T12:01:31Z",
                "观众键": "观5", "点赞增量": 1, "送礼额": 0,
            }],
        }
        with self.assertRaises(RuntimeError):
            self.装(新票)

        conn = self.连接()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM 互动事件 WHERE 事件编号='E9'").fetchone()["c"], 0,
                "分叉票必须整票回滚，事件行不得落库")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM 装载批次 WHERE 编号='分叉后新票'").fetchone()["c"], 0)
        finally:
            conn.close()

    def test_贯验抓缺证明与孤证明(self):
        from app.services import 贯验
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        self.assertTrue(贯验.跑贯验()["是否通过"])

        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 先撤回证明 WHERE 窗键=? AND 事件编号='L1'", (窗键,))
            conn.commit()
        finally:
            conn.close()
        果 = 贯验.跑贯验()
        self.assertFalse(果["是否通过"])
        self.assertTrue(any("证明" in q for q in 果["问题"]))

        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 撤回单 WHERE 窗键=? AND 事件编号='L1'", (窗键,))
            conn.execute("DELETE FROM 撤回幂等审计 WHERE 锚点键=?", (f"{窗键}|L1",))
            conn.execute(
                """INSERT INTO 先撤回证明(事件编号, 窗键, 首撤哈希, 证明号, 时刻)
                   VALUES ('L1', ?, '假哈希', '证-孤例', '2026-11-22T12:01:20Z')""",
                (窗键,),
            )
            conn.commit()
        finally:
            conn.close()
        果2 = 贯验.跑贯验()
        self.assertFalse(果2["是否通过"])
        self.assertTrue(any("孤" in q or "分叉" in q or "无对应撤回行" in q for q in 果2["问题"]), 果2["问题"])

    def test_旧库自愈_空桩时代脏账迁移补齐证明(self):
        """模拟空桩时代旧库：有撤回无证明。初始化迁移须回填证明与审计证明号，核证转 valid。"""
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 先撤回证明")
            conn.execute("UPDATE 撤回幂等审计 SET 证明号=''")
            conn.commit()
        finally:
            conn.close()
        self.assertFalse(self.核证("L1")["valid"])

        from app import seed
        seed.初始化库()

        后 = self.快照()
        self.assertEqual(后["证明行"], 1, "迁移须按撤回簿回填证明")
        self.assertEqual((后["点赞"], 后["送礼"], 后["笔数"]), (7, 50, 3))
        果 = self.核证("L1")
        self.assertTrue(果["valid"], 果["问题"])

        r = self.装(self.读夹具("迟到修正重放样例"))
        self.assertEqual(r["幂等命中"], 1, "自愈后重放仍按证明号消化，不再加累计")
        self.assertEqual(self.快照()["点赞"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
