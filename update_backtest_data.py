"""백테스트 페이지의 입력 파일(backtest_data.js)에 새 거래일을 이어붙인다.

  - c : SOXL 종가 (2021-03-02 분할 이후 구간이라 그대로 쓴다)
  - p : 전 거래일 종가
  - q : 그날 적용되는 QQQ  주간 RSI(14) — 직전 주 마지막 거래일 값
  - x : 그날 적용되는 SOXX 주간 RSI(14) — 같은 방식
  - s : 주식분할 표시 (새 행은 항상 0)

**기존 행은 고치지 않고 새 거래일만 붙인다(append-only).** 과거 구간은
확정 규칙 리포지토리의 make_backtest_data.py 가 만든 값 그대로 두어야
규칙서 §11 수치가 페이지에서도 재현된다.

겹치는 구간의 SOXL 종가가 0.5% 넘게 다르면 주식분할·리비전 신호로 보고
아무것도 쓰지 않고 실패한다. 미확정 종가(NaN)를 만나면 그 앞까지만 붙인다.

종료코드 0 = 갱신함 / 10 = 이미 최신(변경 없음) / 1 = 실패
"""
import io, json, sys
from datetime import date, timedelta

from update_market import _frame, weekly_rsi

OUT = "backtest_data.js"
HEAD = "/* dongpa 백테스트 입력 — make_backtest_data.py 가 만듭니다. 직접 고치지 마세요. */\n"
DRIFT_TOL = 0.005


def read():
    txt = io.open(OUT, encoding="utf-8").read()
    i, j = txt.index("var BT=") + len("var BT="), txt.rindex(";")
    return json.loads(txt[i:j])


def write(bt):
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEAD)
        f.write("var BT=")
        json.dump(bt, f, separators=(",", ":"))
        f.write(";\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bt = read()
    last = bt["d"][-1]
    have = dict(zip(bt["d"], bt["c"]))

    df = _frame("SOXL", "6mo")
    if df.empty:
        raise RuntimeError("SOXL 가격 데이터 없음")

    # 겹치는 구간 검증 — 분할이 나면 과거 종가가 통째로 바뀐다
    for ts, c in df["Close"].items():
        d = ts.strftime("%Y-%m-%d")
        if d in have and c == c and abs(c - have[d]) / have[d] > DRIFT_TOL:
            raise RuntimeError(f"{d} 종가가 기존 값과 다릅니다 ({have[d]} -> {float(c):.6f}). "
                               f"주식분할·리비전일 수 있어 갱신하지 않습니다 — "
                               f"make_backtest_data.py 로 다시 만드세요.")

    new = []
    for ts, c in df["Close"].items():
        d = ts.strftime("%Y-%m-%d")
        if d <= last:
            continue
        if c != c:                                   # 미확정 종가 — 여기서 멈춘다
            print(f"  {d} 종가 미확정 — 그 앞까지만 붙입니다")
            break
        new.append((d, float(c)))
    if not new:
        print(f"이미 최신 ({last})")
        return 10

    qqq, soxx = weekly_rsi("QQQ"), weekly_rsi("SOXX")
    prev = bt["c"][-1]
    added = 0
    for d, c in new:
        pk = (date.fromisoformat(d) - timedelta(days=7)).isocalendar()[:2]
        q, x = qqq.get(pk), soxx.get(pk)
        if not q or not x or q[1] != q[1] or x[1] != x[1]:
            raise RuntimeError(f"{d} 에 적용할 주간 RSI 없음 ({pk})")
        bt["d"].append(d)
        bt["c"].append(round(c, 6))
        bt["p"].append(prev)
        bt["q"].append(round(q[1], 4))
        bt["x"].append(round(x[1], 4))
        bt["s"].append(0)
        prev = bt["c"][-1]
        added += 1

    write(bt)
    print(f"{OUT}  +{added}행  ({new[0][0]} ~ {new[-1][0]})  총 {len(bt['d']):,}행")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"실패: {type(e).__name__}: {e}")
        sys.exit(1)
