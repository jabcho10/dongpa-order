"""동파 주문 지시서의 시세 파일(docs/market.json) 갱신

  - 전일 SOXL 종가  : 가장 최근 거래일 종가
  - QQQ 주간 RSI(14): Adj Close -> 주별 마지막 거래일 -> Wilder 평활
  - 주문일          : 최근 거래일의 다음 영업일
  - weekEnd         : 주문일이 속한 주의 직전 주 마지막 거래일 (RSI 산출 기준일)

페이지는 이 파일을 같은 폴더에서 읽는다. 읽지 못하면 HTML 내장 대체값으로 동작한다.

종료코드 0 = 갱신함 / 10 = 이미 최신(변경 없음) / 1 = 실패
"""
import io, json, os, sys, time
from datetime import datetime, timedelta, timezone

OUT = os.path.join("docs", "market.json")
N = 14
KST = timezone(timedelta(hours=9))


def _frame(ticker, period):
    import yfinance as yf, pandas as pd
    df = yf.download(ticker, period=period, interval="1d",
                     auto_adjust=False, progress=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df


def weekly_rsi(ticker, n=N):
    px = _frame(ticker, "3y")["Adj Close"].dropna()
    if px.empty:
        raise RuntimeError(f"{ticker} 가격 데이터 없음")
    iso = px.index.isocalendar()
    keys = list(zip(iso["year"], iso["week"]))
    last = {}
    for d, k in zip(px.index, keys):
        last[k] = d                                  # 주별 마지막 거래일
    wk = px.loc[[last[k] for k in dict.fromkeys(keys)]]
    d = wk.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + au / ad)
    return {k: (last[k], float(rsi.loc[last[k]])) for k in last}


def latest_close(ticker, tries=3, wait=15):
    """가장 최근 거래일의 '확정된' 종가를 돌려준다.

    Yahoo는 방금 마감된 세션의 봉을 Open·Volume만 채운 채 Close=NaN 으로
    잠시 내려준다. 이때 NaN을 건너뛰고 그 전날 종가를 쓰면 이미 지나간 세션용
    주문을 산출하게 되므로, 재시도 후에도 확정되지 않으면 실패로 처리한다.
    """
    import pandas as pd
    last = None
    for i in range(tries):
        s = _frame(ticker, "1mo")["Close"]
        if s.empty:
            raise RuntimeError(f"{ticker} 종가 데이터 없음")
        last = s
        if pd.notna(s.iloc[-1]):
            return s.index[-1].date(), float(s.iloc[-1])
        if i < tries - 1:
            print(f"  {ticker} 최신 봉({s.index[-1]:%Y-%m-%d}) 종가 미확정 — "
                  f"{wait}초 후 재시도 ({i + 1}/{tries - 1})")
            time.sleep(wait)

    pending = last.index[-1].date()
    good = last.dropna()
    if good.empty:
        raise RuntimeError(f"{ticker} 확정 종가 없음")
    raise RuntimeError(
        f"{ticker} {pending} 종가가 아직 확정되지 않았습니다 "
        f"(직전 확정 종가는 {good.index[-1].date()}). "
        f"지나간 세션용 주문을 만들지 않기 위해 갱신을 건너뜁니다 — "
        f"잠시 후 워크플로를 다시 실행하세요.")


def next_business_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    base_date, prev = latest_close("SOXL")

    # 신선도 가드: 주말·공휴일을 감안해도 4일을 넘는 공백은 데이터 이상으로 본다.
    age = (datetime.now(timezone.utc).date() - base_date).days
    if age > 4:
        raise RuntimeError(f"최근 확정 종가가 {age}일 전({base_date})입니다 — "
                           f"데이터 이상으로 보고 갱신을 건너뜁니다.")

    order_date = next_business_day(base_date)

    wk = weekly_rsi("QQQ")
    prior = order_date - timedelta(days=7)           # 주문일 직전 주
    key = (prior.isocalendar()[0], prior.isocalendar()[1])
    if key not in wk:
        raise RuntimeError(f"주간 RSI 없음: {key}")
    week_end, rsi = wk[key]
    if rsi != rsi:
        raise RuntimeError("주간 RSI 계산 불가 (NaN)")

    data = {
        "prev": round(prev, 2),
        "rsi": round(rsi, 4),
        "baseDate": str(base_date),
        "orderDate": str(order_date),
        "weekEnd": week_end.strftime("%Y-%m-%d"),
        "updated": f"{datetime.now(KST):%Y-%m-%d %H:%M} KST",
    }

    mode = "공세" if data["rsi"] >= 50 else "수비"
    cap = data["prev"] * (1.05 if data["rsi"] >= 50 else 1.03)
    print(f"SOXL {data['baseDate']} 종가 ${data['prev']:.2f} | "
          f"QQQ 주간RSI {data['rsi']:.4f} ({data['weekEnd']}) -> {mode}모드")
    print(f"주문일 {data['orderDate']} | LOC 매수 상한 ${cap:.2f}")

    old = None
    if os.path.exists(OUT):
        try:
            old = json.load(io.open(OUT, encoding="utf-8"))
        except Exception:
            old = None
    if old:
        old.pop("updated", None)
        cur = dict(data)
        cur.pop("updated", None)
        if old == cur:
            print("변경 없음")
            return 10

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{OUT} 갱신 완료")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"실패: {type(e).__name__}: {e}")
        sys.exit(1)
