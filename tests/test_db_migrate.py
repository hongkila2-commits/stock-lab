"""예전 DB(picks·explain 에 horizon 칸 없음)를 열면 데이터를 보존한 채 새 형식으로 바뀐다."""
import sqlite3

from stocklab import db


def test_old_picks_and_explain_are_kept_as_5_day(tmp_path):
    path = tmp_path / "old.sqlite"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE picks (asof TEXT, code TEXT, rank INTEGER, prob REAL, PRIMARY KEY (asof, code));
        CREATE TABLE explain (asof TEXT, code TEXT, feature TEXT, value REAL, pct REAL, contrib REAL,
                              PRIMARY KEY (asof, code, feature));
        INSERT INTO picks VALUES ('2026-09-25', '005930', 1, 0.61), ('2026-09-25', '000660', 2, 0.58);
        INSERT INTO explain VALUES ('2026-09-25', '005930', 'ret_5', 0.1, 0.9, 0.2);
    """)
    c.commit()
    c.close()

    conn = db.connect(path)
    assert "horizon" in db._columns(conn, "picks") and "horizon" in db._columns(conn, "explain")
    picks = db.query(conn, "SELECT * FROM picks ORDER BY rank")
    assert picks["horizon"].tolist() == [5, 5] and picks["code"].tolist() == ["005930", "000660"]
    assert db.query(conn, "SELECT horizon, feature FROM explain").values.tolist() == [[5, "ret_5"]]
    # 같은 날 같은 종목을 1개월(20) 추천에도 넣을 수 있어야 한다
    conn.execute("INSERT INTO picks VALUES ('2026-09-25', 20, '005930', 1, 0.7)")
    conn.commit()
    conn.close()

    conn = db.connect(path)                       # 두 번째 열기: 아무 변화 없음
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 3
    assert not [t for t in db.query(conn, "SELECT name FROM sqlite_master")["name"] if t.endswith("_old_v1")]
