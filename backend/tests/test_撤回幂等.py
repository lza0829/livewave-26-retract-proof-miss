"""撤回幂等键（口径B）测试。

覆盖题面夹具：
1. 迟到修正成功后原包重放——窗合计不变；互动事件表仍一行、撤回单仍一行，且有幂等命中审计；
2. 重放与关窗扫描并发交错——不得半截双加（事件行/撤回行恒1、合计不双加、贯验通过）；
3. 只读接口 /api/撤回幂等 的字段与 valid 口径；
4. 失败闭：人为破坏锚点后重放必须抛错回滚，不得静默双计。

运行：python -m unittest backend.tests.test_撤回幂等 -v
（用任意带 fastapi 的解释器；CI 里先 pip install -r backend/requirements.txt httpx）
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


class 幂等基类(unittest.TestCase):
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

    def 快照(self, 窗键):
        conn = self.连接()
        try:
            窗 = conn.execute("SELECT * FROM 热度窗 WHERE 窗键=?", (窗键,)).fetchone()
            事件行 = conn.execute(
                "SELECT COUNT(*) AS c FROM 互动事件 WHERE 事件编号='L1'"
            ).fetchone()["c"]
            撤回行 = conn.execute(
                "SELECT COUNT(*) AS c FROM 撤回单 WHERE 窗键=? AND 事件编号='L1' AND 原因='迟到修正'",
                (窗键,),
            ).fetchone()["c"]
            审计 = conn.execute(
                "SELECT * FROM 撤回幂等审计 WHERE 锚点键=?", (f"{窗键}|L1",)
            ).fetchone()
            return {
                "点赞": 窗["点赞合计"], "送礼": 窗["送礼合计"], "笔数": 窗["事件笔数"],
                "状态": 窗["状态"], "版本": 窗["版本"],
                "事件行": 事件行, "撤回行": 撤回行,
                "命中": int(审计["命中次数"]) if 审计 else 0,
            }
        finally:
            conn.close()

    def 贯验(self):
        from app.services import 贯验
        return 贯验.跑贯验()

    def 幂等查(self, 事件编号, 窗键=""):
        from app.services import 撤回幂等
        conn = self.连接()
        try:
            return 撤回幂等.查只读(conn, 事件编号, 窗键 or None)
        finally:
            conn.close()


class 重放幂等(幂等基类):
    def test_迟到修正后原包重放_合计不变_行恒一(self):
        准时 = self.读夹具("准时热度样例")
        迟到 = self.读夹具("迟到修正样例")
        重放 = self.读夹具("迟到修正重放样例")
        窗键 = "房A|2026-11-22T12:00:00Z"

        r1 = self.装(准时)
        r2 = self.装(迟到)
        self.assertEqual(r2["撤回"], 1)
        修正后 = self.快照(窗键)
        self.assertEqual(修正后["状态"], "已关闭")
        self.assertEqual(修正后["事件行"], 1)
        self.assertEqual(修正后["撤回行"], 1)
        self.assertEqual(修正后["命中"], 1)
        
        self.assertEqual((修正后["点赞"], 修正后["送礼"], 修正后["笔数"]), (7, 50, 3))

        
        r3 = self.装(重放)
        self.assertEqual(r3["幂等命中"], 1)
        self.assertEqual(r3["撤回"], 0)
        self.assertEqual(r3["入流"], 0)
        重放后 = self.快照(窗键)
        
        self.assertEqual(重放后["点赞"], 修正后["点赞"])
        self.assertEqual(重放后["送礼"], 修正后["送礼"])
        self.assertEqual(重放后["笔数"], 修正后["笔数"])
        self.assertEqual(重放后["事件行"], 1)
        self.assertEqual(重放后["撤回行"], 1)
        self.assertEqual(重放后["命中"], 2)

        
        self.装(json.loads(json.dumps(重放, ensure_ascii=False)) | {"批次编号": "批-重放-2"})
        三放 = self.快照(窗键)
        self.assertEqual((三放["点赞"], 三放["送礼"], 三放["笔数"]), (7, 50, 3))
        self.assertEqual((三放["事件行"], 三放["撤回行"]), (1, 1))
        self.assertEqual(三放["命中"], 3)

        果 = self.贯验()
        self.assertTrue(果["是否通过"], 果["说明"])

    def test_同批次编号原包再推_批次不撞车_仍幂等(self):
        """装载批次主键不得让原包重放整体失败：编号自动加后缀，事件/撤回/合计仍恒一。"""
        迟到 = self.读夹具("迟到修正样例")
        self.装(self.读夹具("准时热度样例"))
        r1 = self.装(迟到)
        r2 = self.装(json.loads(json.dumps(迟到, ensure_ascii=False)))  
        r3 = self.装(json.loads(json.dumps(迟到, ensure_ascii=False)))
        self.assertEqual(r1["批次编号"], "批-迟到-01")
        self.assertEqual(r2["批次编号"], "批-迟到-01#重放1")
        self.assertEqual(r3["批次编号"], "批-迟到-01#重放2")
        self.assertEqual((r2["撤回"], r3["撤回"]), (0, 0))
        self.assertEqual((r2["幂等命中"], r3["幂等命中"]), (1, 1))
        快照 = self.快照("房A|2026-11-22T12:00:00Z")
        self.assertEqual((快照["事件行"], 快照["撤回行"]), (1, 1))
        self.assertEqual((快照["点赞"], 快照["送礼"], 快照["笔数"]), (7, 50, 3))
        conn = self.连接()
        try:
            批 = conn.execute(
                "SELECT COUNT(*) c FROM 装载批次 WHERE 编号 LIKE '批-迟到-01%'"
            ).fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(批, 3)

    def test_只读接口_字段与valid(self):
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        self.装(self.读夹具("迟到修正重放样例"))
        窗键 = "房A|2026-11-22T12:00:00Z"

        果 = self.幂等查("L1", 窗键)
        self.assertTrue(果["found"])
        self.assertTrue(果["是否二次"])
        self.assertEqual(果["幂等命中"], 2)
        self.assertEqual(果["撤回行数"], 1)
        self.assertEqual(果["事件行数"], 1)
        self.assertEqual(果["合计"], {"点赞": 7, "送礼": 50, "笔数": 3})
        self.assertTrue(果["valid"], 果["问题"])
        self.assertEqual(果["锚点口径"], "窗键+事件编号撤回幂等")

        
        果2 = self.幂等查("L1")
        self.assertEqual(果2["窗键"], 窗键)
        self.assertTrue(果2["valid"])

        
        果3 = self.幂等查("E1")
        self.assertTrue(果3["valid"], 果3["问题"])
        self.assertEqual(果3["撤回行数"], 0)
        self.assertFalse(果3["是否二次"])

    def test_HTTP只读接口(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            self.assertEqual(c.post("/api/装载/夹具", params={"名": "准时热度样例"}).status_code, 200)
            self.assertEqual(c.post("/api/装载/夹具", params={"名": "迟到修正样例"}).status_code, 200)
            self.assertEqual(c.post("/api/装载/夹具", params={"名": "迟到修正重放样例"}).status_code, 200)
            r = c.get("/api/撤回幂等", params={"事件编号": "L1"})
            self.assertEqual(r.status_code, 200)
            b = r.json()
            self.assertTrue(b["valid"])
            self.assertTrue(b["是否二次"])
            self.assertEqual(b["撤回行数"], 1)
            self.assertEqual(b["合计"]["点赞"], 7)
            
            self.assertEqual(c.get("/api/撤回幂等", params={"事件编号": "不存在"}).status_code, 404)


class 并发交错(幂等基类):
    def test_重放与关窗扫描并发_不半截不双加(self):
        """N 线程并发交错误推同一迟到事件（含首推），跑完后恒：事件行1、撤回行1、合计只加一次。"""
        窗键 = "房A|2026-11-22T12:00:00Z"
        迟到 = self.读夹具("迟到修正样例")
        准时 = self.读夹具("准时热度样例")

        屏障 = threading.Barrier(4)
        错误 = []

        def 工(票, 前缀):
            try:
                屏障.wait()
                self.装(json.loads(json.dumps(票, ensure_ascii=False)) | {"批次编号": 前缀 + str(uuid.uuid4())[:8]})
            except Exception as e:  
                错误.append(repr(e))

        
        e1 = {"批次编号": "setup-1", "事件": [准时["事件"][0]]}
        e3 = {"批次编号": "setup-2", "事件": [准时["事件"][2]]}
        self.装(e1)
        self.装(e3)
        self.assertEqual(self.快照(窗键)["状态"], "已关闭")
        关前 = self.快照(窗键)

        线程 = []
        for i in range(4):
            t = threading.Thread(target=工, args=(迟到, f"并发-{i}-"))
            线程.append(t)
        for t in 线程:
            t.start()
        for t in 线程:
            t.join()

        快照 = self.快照(窗键)
        
        self.assertEqual(快照["事件行"], 1)
        self.assertEqual(快照["撤回行"], 1)
        
        self.assertEqual(快照["点赞"], 关前["点赞"] + 4)
        self.assertEqual(快照["笔数"], 关前["笔数"] + 1)
        
        self.assertEqual(快照["命中"], 4 - sum(1 for e in 错误 if "locked" in e.lower()))
        self.assertGreaterEqual(快照["命中"], 1)

        果 = self.贯验()
        self.assertTrue(果["是否通过"], 果["说明"])

        
        conn = self.连接()
        try:
            双 = conn.execute(
                """SELECT 窗键, 事件编号, COUNT(*) c FROM 撤回单
                   WHERE 原因='迟到修正' GROUP BY 窗键, 事件编号 HAVING c>1"""
            ).fetchall()
            self.assertEqual(双, [])
        finally:
            conn.close()

    def test_首推迟到与准时关窗同批交错_单票不双计(self):
        """同一批里关窗扫描先发生、迟到修正后发生：锚点+合计+撤回+事件四写同事务，要么全成要么全不成。"""
        窗键 = "房A|2026-11-22T12:00:00Z"
        准时 = self.读夹具("准时热度样例")
        迟到 = self.读夹具("迟到修正样例")
        合并 = {
            "批次编号": "交错单批",
            "事件": 准时["事件"] + 迟到["事件"],
        }
        r = self.装(合并)
        self.assertEqual(r["撤回"], 1)
        快照 = self.快照(窗键)
        self.assertEqual((快照["事件行"], 快照["撤回行"]), (1, 1))
        self.assertEqual((快照["点赞"], 快照["送礼"], 快照["笔数"]), (7, 50, 3))
        self.assertTrue(self.贯验()["是否通过"])


class 失败闭(幂等基类):
    def test_缺锚点半截账_重放必须报错回滚(self):
        """模拟空桩时代脏账：撤回单已写但撤回锚点丢失（有撤回无锚的半截）。重放须抛错且整事务回滚，合计不动。"""
        self.装(self.读夹具("准时热度样例"))
        self.装(self.读夹具("迟到修正样例"))
        窗键 = "房A|2026-11-22T12:00:00Z"
        前 = self.快照(窗键)

        conn = self.连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM 撤回幂等审计 WHERE 锚点键=?", (f"{窗键}|L1",))
            conn.commit()
        finally:
            conn.close()
        
        conn = self.连接()
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) c FROM 撤回单 WHERE 窗键=? AND 事件编号='L1'", (窗键,)
                ).fetchone()["c"],
                1,
            )
        finally:
            conn.close()

        with self.assertRaises(RuntimeError):
            self.装(self.读夹具("迟到修正重放样例"))

        后 = self.快照(窗键)
        self.assertEqual((后["点赞"], 后["送礼"], 后["笔数"]), (前["点赞"], 前["送礼"], 前["笔数"]))
        self.assertEqual((后["事件行"], 后["撤回行"]), (1, 1))

    def test_旁路事件重放_不重复灌旁路匣(self):
        self.装(self.读夹具("准时热度样例"))
        
        超迟 = self.读夹具("超迟到旁路样例")
        r1 = self.装(超迟)
        self.assertGreaterEqual(r1["旁路"], 1)
        conn = self.连接()
        try:
            匣前 = conn.execute("SELECT COUNT(*) c FROM 旁路匣 WHERE 原因='超允许迟到'").fetchone()["c"]
        finally:
            conn.close()
        r2 = self.装(json.loads(json.dumps(超迟, ensure_ascii=False)) | {"批次编号": "超迟重放"})
        
        self.assertEqual(r2["幂等命中"], 3)
        conn = self.连接()
        try:
            匣后 = conn.execute("SELECT COUNT(*) c FROM 旁路匣 WHERE 原因='超允许迟到'").fetchone()["c"]
            事件行 = conn.execute("SELECT COUNT(*) c FROM 互动事件 WHERE 事件编号='X1'").fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(匣后, 匣前)
        self.assertEqual(事件行, 1)
        self.assertTrue(self.贯验()["是否通过"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
