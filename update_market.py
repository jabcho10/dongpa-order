"""동파 주문 지시서의 시세 파일(market.json) 갱신

  - 전일 SOXL 종가  : 가장 최근 거래일 종가
  - QQQ 주간 RSI(14): Adj Close -> 주별 마지막 거래일 -> Wilder 평활
  - 주문일          : 최근 거래일의 다음 영업일
  - weekEnd         : 주문일이 속한 주의 직전 주 마지막 거래일 (RSI 산출 기준일)
  - history         : 최근 거래일의 확정 종가와 그날 적용 RSI (페이지 자동 반영용)

페이지는 이 파일을 같은 폴더에서 읽는다. 읽지 못하면 HTML 내장 대체값으로 동작한다.

종료코드 0 = 갱신함 / 10 = 이미 최신(변경 없음) / 1 = 실패
"""
import io, json, os, sys, time
from datetime import datetime, timedelta, timezone

OUT = "market.json"
N = 14
HIST = 60          # history 에 담을 최근 거래일 수
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


def _quote_close(ticker, pending, prev_close):
    """일봉 Close가 비어 있을 때 마감 시세(quote)에서 확정 종가를 가져온다.

    Yahoo는 정규장이 끝난 뒤에도 일봉의 Close를 몇 시간씩 비워두는 일이 잦은데,
    quote 쪽 regularMarketPrice 는 마감 직후 확정된다. 다만 장중 값을 종가로
    쓰면 안 되므로 다음을 모두 만족할 때만 채택한다.

      - 정규장이 이미 끝났다 (marketState)
      - 그 가격의 시각이 pending(= Close가 빈 봉)의 거래일이다
      - quote 가 보는 직전 종가가 우리가 확인한 직전 거래일 종가와 같다

    하나라도 어긋나면 None 을 돌려주고, 호출한 쪽이 실패로 처리한다.
    """
    import pandas as pd, yfinance as yf
    q = yf.Ticker(ticker).info
    if str(q.get("marketState") or "") not in ("POST", "POSTPOST", "CLOSED",
                                               "PRE", "PREPRE"):
        return None                                  # 정규장이 아직 안 끝났다
    px, ts = q.get("regularMarketPrice"), q.get("regularMarketTime")
    if px is None or ts is None:
        return None
    if not isinstance(ts, (int, float)):
        ts = pd.Timestamp(ts).timestamp()
    tz = q.get("exchangeTimezoneName") or "America/New_York"
    when = pd.Timestamp(int(ts), unit="s", tz="UTC").tz_convert(tz)
    if when.date() != pending:                       # 이 거래일의 가격이 아니다
        return None
    ref = q.get("regularMarketPreviousClose")
    if ref is None or abs(float(ref) - prev_close) > 0.005:
        return None                                  # 직전 종가가 안 맞으면 믿지 않는다
    return float(px)


def closes(ticker, period="6mo", tries=3, wait=15):
    """확정된 종가만 담은 시리즈를 돌려준다 (마지막 값 = 최근 거래일 종가).

    Yahoo는 방금 마감된 세션의 봉을 Open·Volume만 채운 채 Close=NaN 으로
    잠시 내려준다. 이때 NaN을 건너뛰고 그 전날 종가를 쓰면 이미 지나간 세션용
    주문을 산출하게 되므로, 재시도 -> 마감 시세 보완 순으로 확정 종가를 구하고
    그래도 얻지 못하면 실패로 처리한다.
    """
    import pandas as pd
    s = None
    for i in range(tries):
        s = _frame(ticker, period)["Close"]
        if s.empty:
            raise RuntimeError(f"{ticker} 종가 데이터 없음")
        if pd.notna(s.iloc[-1]):
            return s.dropna()
        if i < tries - 1:
            print(f"  {ticker} 최신 봉({s.index[-1]:%Y-%m-%d}) 종가 미확정 — "
                  f"{wait}초 후 재시도 ({i + 1}/{tries - 1})")
            time.sleep(wait)

    pending = s.index[-1].date()
    good = s.dropna()
    if good.empty:
        raise RuntimeError(f"{ticker} 확정 종가 없음")

    px = _quote_close(ticker, pending, float(good.iloc[-1]))
    if px is None:
        raise RuntimeError(
            f"{ticker} {pending} 종가가 아직 확정되지 않았습니다 "
            f"(직전 확정 종가는 {good.index[-1].date()}). "
            f"지나간 세션용 주문을 만들지 않기 위해 갱신을 건너뜁니다 — "
            f"잠시 후 워크플로를 다시 실행하세요.")
    print(f"  {ticker} {pending} 일봉 Close 미확정 — 마감 시세로 보완 (${px:.2f})")
    return pd.concat([good, pd.Series({s.index[-1]: px})])


def next_business_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    soxl = closes("SOXL")
    base_date, prev = soxl.index[-1].date(), float(soxl.iloc[-1])

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

    # 페이지가 놓친 거래일을 스스로 따라잡을 수 있도록 최근 종가 이력을 싣는다.
    # 각 거래일에 그날 적용되는 주간 RSI(직전 주 마지막 거래일 값)를 함께 넣는다.
    hist = []
    for ts, c in soxl.items():
        hd = ts.date()
        pk = (hd - timedelta(days=7)).isocalendar()[:2]
        if pk not in wk:
            continue
        _we, hr = wk[pk]
        if hr != hr:
            continue
        hist.append({"d": str(hd), "c": round(float(c), 2), "r": round(hr, 4)})
    hist = hist[-HIST:]
    if not hist or hist[-1]["d"] != str(base_date):
        raise RuntimeError(f"history 마지막 날짜가 기준 종가일과 다릅니다 "
                           f"({hist[-1]['d'] if hist else '없음'} != {base_date})")

    data = {
        "prev": round(prev, 2),
        "rsi": round(rsi, 4),
        "baseDate": str(base_date),
        "orderDate": str(order_date),
        "weekEnd": week_end.strftime("%Y-%m-%d"),
        "updated": f"{datetime.now(KST):%Y-%m-%d %H:%M} KST",
        "history": hist,
    }

    mode = "공세" if data["rsi"] >= 50 else "수비"
    cap = data["prev"] * (1.05 if data["rsi"] >= 50 else 1.03)
    print(f"SOXL {data['baseDate']} 종가 ${data['prev']:.2f} | "
          f"QQQ 주간RSI {data['rsi']:.4f} ({data['weekEnd']}) -> {mode}모드")
    print(f"주문일 {data['orderDate']} | LOC 매수 상한 ${cap:.2f}")
    print(f"history {len(hist)}일 ({hist[0]['d']} ~ {hist[-1]['d']})")

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
