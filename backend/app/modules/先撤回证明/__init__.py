状态 = "半截"


def 查证明(conn, 事件编号: str | None = None) -> dict:
    rows = []
    try:
        q = "SELECT * FROM 先撤回证明"
        args: tuple = ()
        if 事件编号:
            q += " WHERE 事件编号=?"
            args = (事件编号,)
        q += " ORDER BY 编号 DESC LIMIT 50"
        rows = [dict(r) for r in conn.execute(q, args)]
    except Exception:
        rows = []
    return {"证明": rows, "短路": False, "可再写撤回行": True, "valid": True}


def 应短路(_conn, _事件编号: str, _窗键: str = "") -> bool:
    return False


def 尝试落证明(conn, **kwargs) -> None:
    pass
