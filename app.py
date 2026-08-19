# ============================================================
# [v39.6] 종목별 맞춤형 동적 가중치(Dynamic Weighting) 모델 탑재 최종 대시보드
#         - 네이버 자동완성 API를 활용한 종목 검색 리스트
#         - 다중 가격 데이터 소스 폴백 (FinanceDataReader → yfinance → Naver)
# ============================================================

import os
import re
import json
import logging
import requests
import numpy as np
import pandas as pd
from io import StringIO
from bs4 import BeautifulSoup
import yfinance as yf
import FinanceDataReader as fdr
from datetime import datetime, timedelta
import streamlit as st
import streamlit.components.v1 as components

logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ---------- 종목 검색 데이터 ----------
# 검색은 네이버 자동완성에만 의존하지 않고 KRX 전체 종목 목록을 함께 사용한다.
# 특히 "부동산"처럼 종목명 중간에 포함된 문자열도 모두 찾는다.
krx_df = pd.DataFrame()


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def load_krx_listing():
    """KRX 전체 종목 목록. FDR 실패 시 네이버 시장별 페이지를 직접 수집."""
    frames = []

    # 1) FinanceDataReader
    for market in ['KRX', 'KOSPI', 'KOSDAQ', 'KONEX', 'KRX-DESC']:
        try:
            df = fdr.StockListing(market)
            if df is not None and not df.empty and 'Code' in df.columns and 'Name' in df.columns:
                out = df[['Code', 'Name']].copy()
                out.columns = ['Code', 'Name']
                out['Code'] = out['Code'].astype(str).str.extract(r'(\d{6})', expand=False)
                out['Name'] = (
                    out['Name'].astype(str)
                    .str.replace(r'<[^>]+>', '', regex=True)
                    .str.strip()
                )
                out = out.dropna(subset=['Code', 'Name'])
                out = out[(out['Code'].str.len() == 6) & (out['Name'] != '')]
                frames.append(out)
        except Exception as e:
            logging.warning(f"FDR {market} 목록 실패: {e}")

    # 2) Naver market sum fallback
    headers = {'User-Agent': 'Mozilla/5.0'}
    for sosok in [0, 1, 2]:
        # 0 KOSPI, 1 KOSDAQ, 2 KONEX
        for page in range(1, 40):
            try:
                url = (
                    "https://finance.naver.com/sise/sise_market_sum.naver"
                    f"?sosok={sosok}&page={page}"
                )
                res = requests.get(url, headers=headers, timeout=7)
                res.encoding = 'cp949'
                tables = pd.read_html(StringIO(res.text), encoding='cp949')
                if not tables:
                    break

                table = None
                for t in tables:
                    cols = [str(c) for c in t.columns]
                    if '종목명' in cols:
                        table = t
                        break
                if table is None:
                    break

                name_col = '종목명'
                code_col = '종목코드' if '종목코드' in table.columns else None
                if code_col is None:
                    # 링크의 item/main.naver?code=XXXXXX에서 코드 추출
                    soup = BeautifulSoup(res.text, 'html.parser')
                    rows = []
                    for a in soup.select('a.tltle'):
                        name = a.get_text(strip=True)
                        href = a.get('href', '')
                        mm = re.search(r'code=(\d{6})', href)
                        if mm and name:
                            rows.append({'Code': mm.group(1), 'Name': name})
                    if rows:
                        frames.append(pd.DataFrame(rows))
                else:
                    tmp = table[[code_col, name_col]].copy()
                    tmp.columns = ['Code', 'Name']
                    tmp['Code'] = tmp['Code'].astype(str).str.extract(r'(\d{6})', expand=False)
                    tmp['Name'] = tmp['Name'].astype(str).str.strip()
                    frames.append(tmp)

                # 페이지가 비어 있으면 종료
                if len(table) == 0:
                    break
            except Exception as e:
                logging.warning(f"Naver 시장목록 실패 sosok={sosok}, page={page}: {e}")
                break

    # 3) Naver ETF 목록 API
    try:
        etf_url = "https://finance.naver.com/api/sise/etfItemList.nhn"
        res = requests.get(etf_url, headers=headers, timeout=7)
        data = res.json()
        items = data.get("result", {}).get("etfItemList", [])
        if items:
            frames.append(pd.DataFrame([
                {"Code": str(x.get("itemcode", "")), "Name": str(x.get("itemname", ""))}
                for x in items
                if x.get("itemcode") and x.get("itemname")
            ]))
    except Exception as e:
        logging.warning(f"Naver ETF 목록 실패: {e}")


    if not frames:
        return pd.DataFrame(columns=['Code', 'Name'])

    result = pd.concat(frames, ignore_index=True)
    result = result.dropna(subset=['Code', 'Name'])
    result['Code'] = result['Code'].astype(str).str.extract(r'(\d{6})', expand=False)
    result['Name'] = (
        result['Name'].astype(str)
        .str.replace(r'<[^>]+>', '', regex=True)
        .str.strip()
    )
    result = result[
        result['Code'].str.fullmatch(r'\d{6}', na=False)
        & (result['Name'] != '')
    ]
    return result.drop_duplicates('Code').reset_index(drop=True)


def search_naver_autocomplete(query):
    """네이버 자동완성 검색. 실패해도 전체 검색은 계속 진행한다."""
    url = "https://ac.finance.naver.com/ac"
    params = {
        "q": query,
        "target": "stock",
        "mode": "json"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        res.raise_for_status()
        data = res.json()
        result = []
        for item in data.get('items', []):
            if isinstance(item, list) and len(item) >= 2:
                code = str(item[0]).strip()
                name = re.sub(r'<[^>]+>', '', str(item[1])).strip()
                if re.fullmatch(r'\d{6}', code) and name:
                    result.append({'code': code, 'name': name})
        return result
    except Exception as e:
        logging.warning(f"네이버 자동완성 검색 실패: {e}")
        return []


def search_stock_list(query, max_results=200):
    """
    종목명/코드 검색의 통합 엔진.

    우선순위:
      1. 네이버 자동완성 결과
      2. KRX 전체 목록에서 '종목명에 검색어가 포함'된 모든 종목

    예:
      '부동산' -> 이름에 '부동산'이 들어있는 KRX 종목을 전부 반환
      '하이닉스' -> SK하이닉스 등 부분일치 결과 반환
      '005930' -> 삼성전자 반환
    """
    query = str(query or '').strip()
    if not query:
        return []

    # 숫자 코드 검색
    if query.isdigit():
        qcode = query.zfill(6)
        if len(qcode) == 6:
            df = load_krx_listing()
            if not df.empty:
                hit = df[df['Code'] == qcode]
                if not hit.empty:
                    return [
                        {'code': str(r.Code), 'name': str(r.Name)}
                        for r in hit.itertuples(index=False)
                    ]
            # KRX 목록이 일시적으로 실패하면 네이버 자동완성도 시도
            return search_naver_autocomplete(query)
        return []

    q = query.casefold()
    results = []

    # 1) 네이버 자동완성 결과를 먼저 확보
    results.extend(search_naver_autocomplete(query))

    # 2) KRX 전체 목록에서 '종목명 부분일치' 검색
    df = load_krx_listing()
    if not df.empty:
        names = df['Name'].astype(str)
        mask = names.str.casefold().str.contains(q, regex=False, na=False)
        hits = df.loc[mask, ['Code', 'Name']]

        # 검색어가 이름에 정확히/앞부분에 가까운 종목을 먼저 배치
        exact = hits[hits['Name'].str.casefold() == q]
        starts = hits[
            hits['Name'].str.casefold().str.startswith(q) &
            (hits['Name'].str.casefold() != q)
        ]
        contains = hits[
            ~hits['Name'].str.casefold().str.startswith(q)
        ]

        ordered = pd.concat([exact, starts, contains], ignore_index=True)
        results.extend(
            {'code': str(r.Code), 'name': str(r.Name)}
            for r in ordered.itertuples(index=False)
        )

    # 중복 제거 + 최대 결과 수 제한
    unique = []
    seen = set()
    for item in results:
        code = str(item.get('code', '')).strip()
        name = str(item.get('name', '')).strip()
        key = code or name
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append({'code': code, 'name': name})
        if len(unique) >= max_results:
            break

    return unique


# ---------- 종목명 & 코드 정밀 매칭 ----------
def get_code_and_name(query):
    global krx_df
    query = str(query or '').strip()

    if query.isdigit() and len(query) == 6:
        results = search_stock_list(query, max_results=1)
        if results:
            return results[0]['code'], results[0]['name']

        code = query
        try:
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            soup = BeautifulSoup(res.text, 'html.parser')
            name_elem = soup.select_one('.wrap_company h2 a')
            if name_elem:
                return code, name_elem.text.strip()
        except Exception:
            pass
        return code, code

    results = search_stock_list(query, max_results=1)
    if results:
        return results[0]['code'], results[0]['name']

    return None, None


# ---------- 네이버 월봉 가격 데이터 스크래핑 ----------
def _get_naver_monthly_price(code):
    """네이버 금융 월봉 데이터를 스크래핑하여 DataFrame 반환"""
    url = f"https://finance.naver.com/item/sise_month.nhn?code={code}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'cp949'
        tables = pd.read_html(StringIO(res.text), encoding='cp949')
        if tables:
            df = tables[0].copy()
            if '날짜' in df.columns:
                df['날짜'] = pd.to_datetime(df['날짜'], format='%Y.%m.%d')
                df = df.set_index('날짜')
                df = df.rename(columns={
                    '시가': 'Open', '고가': 'High', '저가': 'Low', '종가': 'Close', '거래량': 'Volume'
                })
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                df = df.apply(pd.to_numeric, errors='coerce')
                df = df.sort_index()
                return df
    except Exception:
        pass
    return pd.DataFrame()


# ---------- 가격 데이터 다중 소스 폴백 ----------
def get_price_data(code, name, start_date):
    """
    가격 데이터를 다양한 소스에서 순차적으로 시도하여 가져온다.
    Returns: DataFrame with index=Date, columns=['Open','High','Low','Close','Volume']
    """
    # 1) FinanceDataReader (Yahoo)
    try:
        df = fdr.DataReader(code, start=start_date)
        if df is not None and not df.empty:
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            return df
    except Exception as e:
        logging.warning(f"FinanceDataReader failed: {e}")

    # 2) yfinance 직접 호출
    for suffix in ['.KS', '.KQ']:
        try:
            ticker = f"{code}{suffix}"
            yf_ticker = yf.Ticker(ticker)
            df = yf_ticker.history(start=start_date, auto_adjust=False)
            if df is not None and not df.empty:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                df.index = df.index.tz_localize(None)
                return df
        except Exception as e:
            logging.warning(f"yfinance failed for {ticker}: {e}")

    # 3) 네이버 금융 월봉 데이터
    df_naver = _get_naver_monthly_price(code)
    if not df_naver.empty:
        cutoff = datetime.today() - timedelta(days=365 * 11)
        df_naver = df_naver[df_naver.index >= cutoff]
        return df_naver

    return pd.DataFrame()


# ---------- DPS 자동 크롤링 ----------
def get_dps_automatically(code, name):
    for suffix in ['.KS', '.KQ']:
        try:
            t = yf.Ticker(f"{code}{suffix}")
            divs = t.dividends
            if not divs.empty:
                recent_divs = divs[divs.index >= (datetime.today() - timedelta(days=365))]
                if not recent_divs.empty:
                    dps_val = float(recent_divs.sum())
                    if dps_val > 0:
                        return dps_val
        except Exception:
            continue

    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        for em in soup.find_all(['em', 'th', 'td']):
            if '주당배당금' in em.text or '배당금' in em.text:
                nxt = em.find_next_sibling()
                if nxt:
                    nums = re.findall(r'[\d,]+', nxt.text)
                    if nums:
                        val = float(nums[0].replace(',', ''))
                        if val > 10:
                            return val
    except Exception:
        pass

    # 배당 데이터가 없으면 임의의 DPS를 넣지 않는다.
    return np.nan


# ---------- 뉴스 및 공시 ----------
def get_news_and_disclosures(code):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': f'https://finance.naver.com/item/main.naver?code={code}'
    }
    news_list, notice_list = [], []

    try:
        url_news = f"https://finance.naver.com/item/news_news.naver?code={code}&page=1"
        res = requests.get(url_news, headers=headers, timeout=5)
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        for tr in soup.find_all('tr'):
            a = tr.select_one('td.title a')
            if not a:
                continue
            title = a.text.strip()
            href = a.get('href', '')
            link = f"https://finance.naver.com{href}" if href.startswith('/') else href
            info_td = tr.select_one('td.info')
            press = info_td.text.strip() if info_td else "네이버증권"
            date_td = tr.select_one('td.date')
            date_str = date_td.text.strip()[:10] if date_td else ""
            tag = "배당" if any(k in title for k in ['배당', '분배', '주주']) else ("실적" if any(k in title for k in ['실적', '영업', '매출', '순익']) else "뉴스")
            news_list.append({"tag": tag, "title": title, "press": press, "date": date_str, "link": link})
            if len(news_list) >= 10:
                break
    except Exception:
        pass

    try:
        url_notice = f"https://finance.naver.com/item/news_notice.naver?code={code}&page=1"
        res = requests.get(url_notice, headers=headers, timeout=5)
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        for tr in soup.find_all('tr'):
            a = tr.select_one('td.title a')
            if not a:
                continue
            title = a.text.strip()
            href = a.get('href', '')
            link = f"https://finance.naver.com{href}" if href.startswith('/') else href
            info_td = tr.select_one('td.info')
            press = info_td.text.strip() if info_td else "전자공시"
            date_td = tr.select_one('td.date')
            date_str = date_td.text.strip()[:10] if date_td else ""
            tag = "배당공시" if any(k in title for k in ['배당', '분배', '주주총회']) else ("실적공시" if any(k in title for k in ['실적', '매출', '영업', '보고서']) else "공시")
            notice_list.append({"tag": tag, "title": title, "press": press, "date": date_str, "link": link})
            if len(notice_list) >= 10:
                break
    except Exception:
        pass

    return news_list, notice_list



# ---------- v39.6 재무/PEG 엔진 ----------
def _safe_float(v, default=np.nan):
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def _fetch_yf_info(code):
    """Yahoo Finance PER/메타데이터. 실패하면 {}."""
    for suffix in ['.KS', '.KQ']:
        try:
            t = yf.Ticker(f"{code}{suffix}")
            info = t.info or {}
            if info:
                return info, f"Yahoo Finance ({suffix})"
        except Exception as e:
            logging.warning(f"Yahoo info 실패 {code}{suffix}: {e}")
    return {}, "미확인"


def _fetch_naver_per(code):
    """네이버 금융에서 PER/업종 PER을 최대한 방어적으로 추출."""
    result = {"trailing_pe": np.nan, "forward_pe": np.nan, "sector_pe": np.nan, "source": ""}
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=7
        )
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        text = soup.get_text(" ", strip=True)

        # 종목 기본정보 영역의 PER
        patterns = [
            r'PER\s*\(배\)\s*([0-9]+(?:\.[0-9]+)?)',
            r'PER\s*([0-9]+(?:\.[0-9]+)?)\s*배',
            r'PER\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)'
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _safe_float(m.group(1))
                if np.isfinite(v) and 0 < v < 1000:
                    result["trailing_pe"] = v
                    result["source"] = "Naver Finance"
                    break

        # HTML table에서도 PER 행 탐색
        if not np.isfinite(result["trailing_pe"]):
            for tr in soup.select("tr"):
                row_text = tr.get_text(" ", strip=True)
                if "PER" in row_text.upper():
                    nums = re.findall(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', row_text)
                    for num in nums:
                        v = _safe_float(num)
                        if np.isfinite(v) and 0 < v < 1000:
                            result["trailing_pe"] = v
                            result["source"] = "Naver Finance"
                            break
                    if np.isfinite(result["trailing_pe"]):
                        break

        # 업종 PER 링크/텍스트
        sector_patterns = [
            r'업종PER\s*([0-9]+(?:\.[0-9]+)?)',
            r'업종\s*PER\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)',
            r'동일업종\s*PER\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)'
        ]
        for pat in sector_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _safe_float(m.group(1))
                if np.isfinite(v) and 0 < v < 1000:
                    result["sector_pe"] = v
                    break
    except Exception as e:
        logging.warning(f"Naver PER 실패 {code}: {e}")

    return result


def _resolve_per(code, name):
    """
    PER 우선순위:
    1) Yahoo trailingPE
    2) Yahoo forwardPE
    3) Naver PER
    4) Naver 업종 PER
    5) 데이터 없음 → NULL
    """
    info, yf_source = _fetch_yf_info(code)
    naver = _fetch_naver_per(code)

    trailing = _safe_float(info.get("trailingPE"))
    forward = _safe_float(info.get("forwardPE"))
    sector = _safe_float(naver.get("sector_pe"))

    if np.isfinite(trailing) and 0 < trailing < 500:
        return {
            "per": trailing,
            "source": "Yahoo trailingPE",
            "raw_source": yf_source,
            "is_fallback": False
        }

    if np.isfinite(forward) and 0 < forward < 500:
        return {
            "per": forward,
            "source": "Yahoo forwardPE",
            "raw_source": yf_source,
            "is_fallback": False
        }

    nper = _safe_float(naver.get("trailing_pe"))
    if np.isfinite(nper) and 0 < nper < 500:
        return {
            "per": nper,
            "source": "Naver Finance PER",
            "raw_source": "Naver Finance",
            "is_fallback": False
        }

    if np.isfinite(sector) and 0 < sector < 500:
        return {
            "per": sector,
            "source": "Naver 업종 PER",
            "raw_source": "Naver Finance",
            "is_fallback": False
        }

    # 데이터가 없으면 절대 임의값을 넣지 않는다.
    return {
        "per": np.nan,
        "source": None,
        "raw_source": None,
        "is_fallback": False
    }


def _growth_quality_and_reliability(q_dict, q_labels):
    """
    Growth Quality:
      영업이익 성장 50%
      매출 성장     30%
      OPM 추세      20%

    PEG Reliability:
      영업이익 연속성 40
      성장률 일관성   30
      매출-이익 동행  20
      데이터 소스     10

    계산에 필요한 데이터가 없는 경우 해당 항목을 억지로 채우지 않고
    available weight만 재정규화한다.
    """
    result = {
        "growth_rate": np.nan,
        "growth_quality": np.nan,
        "reliability": np.nan,
        "revenue_growth": np.nan,
        "operating_growth": np.nan,
        "opm_trend": np.nan,
        "opm_latest": np.nan,
        "profit_continuity": "데이터 부족",
        "growth_consistency": np.nan,
        "alignment": "데이터 부족",
        "growth_samples": 0
    }

    if not q_dict or len(q_labels) < 2:
        return result

    rev = [float(q_dict[k].get("revenue", np.nan)) for k in q_labels]
    prof = [float(q_dict[k].get("profit", np.nan)) for k in q_labels]
    opm = [
        (prof[i] / rev[i] * 100) if np.isfinite(prof[i]) and np.isfinite(rev[i]) and rev[i] > 0 else np.nan
        for i in range(len(q_labels))
    ]

    # 최근 4개 분기의 YoY: 같은 분기 기준으로 4칸 전 비교
    yoy_profit, yoy_revenue = [], []
    for i in range(4, len(q_labels)):
        p0, p1 = prof[i-4], prof[i]
        r0, r1 = rev[i-4], rev[i]

        if np.isfinite(p0) and np.isfinite(p1) and abs(p0) > 1e-9:
            yoy_profit.append((p1 - p0) / abs(p0) * 100)
        if np.isfinite(r0) and np.isfinite(r1) and abs(r0) > 1e-9:
            yoy_revenue.append((r1 - r0) / abs(r0) * 100)

    recent_profit = yoy_profit[-4:]
    recent_revenue = yoy_revenue[-4:]

    if recent_profit:
        result["operating_growth"] = float(np.median(recent_profit))
    if recent_revenue:
        result["revenue_growth"] = float(np.median(recent_revenue))

    # OPM 추세: 최근 4개 평균과 그 이전 4개 평균의 차이
    valid_opm = [x for x in opm if np.isfinite(x)]
    if valid_opm:
        result["opm_latest"] = float(valid_opm[-1])
    if len(valid_opm) >= 4:
        recent_opm = float(np.mean(valid_opm[-4:]))
        prior_opm = float(np.mean(valid_opm[-8:-4])) if len(valid_opm) >= 8 else float(valid_opm[0])
        result["opm_trend"] = recent_opm - prior_opm

    # Growth Quality: 극단치가 전체 점수를 지배하지 않도록 -50~100으로 제한
    components = []
    weights = []
    if np.isfinite(result["operating_growth"]):
        components.append(float(np.clip(result["operating_growth"], -50, 100)))
        weights.append(0.50)
    if np.isfinite(result["revenue_growth"]):
        components.append(float(np.clip(result["revenue_growth"], -50, 100)))
        weights.append(0.30)
    if np.isfinite(result["opm_trend"]):
        # OPM 변화는 %p. 성장률과 같은 차원으로 과대 반영하지 않도록 5배까지 제한.
        components.append(float(np.clip(result["opm_trend"] * 5.0, -50, 50)))
        weights.append(0.20)

    if components:
        result["growth_quality"] = float(
            np.average(components, weights=weights)
        )
        result["growth_rate"] = result["growth_quality"]

    # Reliability 40: 영업이익 연속성
    rel_scores = []
    if len(prof) >= 4:
        last4 = prof[-4:]
        if all(x > 0 for x in last4):
            if all(last4[i] >= last4[i-1] for i in range(1, 4)):
                continuity_score = 40
                result["profit_continuity"] = "최근 4분기 흑자 + 상승"
            else:
                continuity_score = 30
                result["profit_continuity"] = "최근 4분기 연속 흑자"
        else:
            continuity_score = 10
            result["profit_continuity"] = "최근 4분기 중 적자 존재"
        rel_scores.append(continuity_score)

    # Reliability 30: 성장률 일관성
    if len(recent_profit) >= 2:
        std = float(np.std(recent_profit, ddof=0))
        result["growth_consistency"] = std
        if std <= 10:
            consistency_score = 30
        elif std <= 20:
            consistency_score = 15
        else:
            consistency_score = 5
        rel_scores.append(consistency_score)

    # Reliability 20: 매출-이익 동행
    if recent_profit and recent_revenue:
        pmed = float(np.median(recent_profit))
        rmed = float(np.median(recent_revenue))
        if pmed >= 0 and rmed >= 0:
            alignment_score = 20 if pmed >= rmed else 10
            result["alignment"] = "동행 + 이익 레버리지" if pmed >= rmed else "동행"
        else:
            alignment_score = 0
            result["alignment"] = "성장 방향 엇갈림"
        rel_scores.append(alignment_score)

    # 데이터 소스 점수는 호출부에서 Yahoo/Naver 교차검증 여부를 추가
    # 여기서는 최대 90점까지 산출하고, 호출부에서 +10 또는 +5를 넣는다.
    result["_reliability_base"] = sum(rel_scores)
    result["growth_samples"] = len(recent_profit)

    return result


def get_pure_real_fundamentals(code, name, df_price_full):
    is_etf = (
        '리츠' in name or 'TIGER' in name or 'KODEX' in name or
        'ACE' in name or 'SOL' in name or 'RISE' in name or '맥쿼리' in name
    )
    cur_price = (
        float(df_price_full['Close'].iloc[-1])
        if not df_price_full.empty else np.nan
    )

    per_info = _resolve_per(code, name)

    fin_payload = {
        "is_etf": is_etf,
        "quarterly": {
            "labels": [], "profit": [], "revenue": [], "net": [],
            "opm": [], "prices": [], "growth_yoy": []
        },
        "semiannual": {
            "labels": [], "profit": [], "revenue": [], "net": [],
            "opm": [], "prices": [], "growth_yoy": []
        },
        "annual": {
            "labels": [], "profit": [], "revenue": [], "net": [],
            "opm": [], "prices": [], "growth_yoy": []
        },
        "growth_model": {
            "est_per": per_info["per"],
            "per_source": per_info["source"],
            "per_fallback": False,
            "growth_rate": np.nan,
            "growth_quality": np.nan,
            "peg": np.nan,
            "target_per": np.nan,
            "target_peg_05": np.nan,
            "target_peg_10": np.nan,
            "peg_reliability": np.nan,
            "revenue_growth": np.nan,
            "operating_growth": np.nan,
            "opm_trend": np.nan,
            "opm_latest": np.nan,
            "profit_continuity": "데이터 부족",
            "growth_consistency": np.nan,
            "alignment": "데이터 부족",
            "growth_samples": 0
        }
    }

    if is_etf:
        return fin_payload

    def get_closest_price(date_str):
        try:
            clean_d = re.sub(r'[^\d.]', '', str(date_str)).strip()
            parts = clean_d.split('.')
            if len(parts) == 2:
                target_dt = pd.to_datetime(
                    f"{parts[0]}-{int(parts[1]):02d}-28"
                )
            else:
                target_dt = pd.to_datetime(clean_d)
            sub = df_price_full[df_price_full.index <= target_dt]
            if not sub.empty:
                return int(sub['Close'].iloc[-1])
            return int(df_price_full['Close'].iloc[-1])
        except Exception:
            return int(df_price_full['Close'].iloc[-1])

    q_dict = {}

    # Yahoo quarterly statements
    try:
        for suffix in ['.KS', '.KQ']:
            t = yf.Ticker(f"{code}{suffix}")
            q_inc = t.quarterly_income_stmt
            if q_inc is not None and not q_inc.empty:
                for col in q_inc.columns:
                    lbl = pd.to_datetime(col).strftime('%Y.%m')
                    rev = (
                        float(q_inc.loc['Total Revenue', col] / 1e8)
                        if 'Total Revenue' in q_inc.index
                        and pd.notna(q_inc.loc['Total Revenue', col])
                        else np.nan
                    )
                    prof = (
                        float(q_inc.loc['Operating Income', col] / 1e8)
                        if 'Operating Income' in q_inc.index
                        and pd.notna(q_inc.loc['Operating Income', col])
                        else np.nan
                    )
                    net_m = [
                        idx for idx in q_inc.index
                        if 'Net Income' in str(idx)
                    ]
                    net = (
                        float(q_inc.loc[net_m[0], col] / 1e8)
                        if net_m and pd.notna(q_inc.loc[net_m[0], col])
                        else np.nan
                    )
                    if np.isfinite(rev) or np.isfinite(prof):
                        q_dict[lbl] = {
                            "revenue": round(rev, 0),
                            "profit": round(prof, 0),
                            "net": round(net, 0)
                        }
                if q_dict:
                    break
    except Exception as e:
        logging.warning(f"Yahoo 재무제표 실패 {code}: {e}")

    # Naver fallback
    if not q_dict:
        try:
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            res = requests.get(
                url,
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=7
            )
            tables = pd.read_html(StringIO(res.text), encoding='euc-kr')
            for table in tables:
                if isinstance(table.columns, pd.MultiIndex):
                    t_idx = table.set_index(table.columns[0])
                    q_cols = [
                        c for c in table.columns
                        if '분기' in str(c[0])
                    ]
                    if q_cols:
                        n_lbls = [str(c[1]).strip() for c in q_cols]

                        def parse_n(kw):
                            matches = [
                                i for i in t_idx.index
                                if kw in str(i)
                            ]
                            if matches:
                                row = t_idx.loc[matches[0]][q_cols]
                                vals = []
                                for v in row:
                                    s = re.sub(r'[^\d.-]', '', str(v))
                                    vals.append(
                                        float(s)
                                        if s not in ['', '-', '.']
                                        else np.nan
                                    )
                                return vals
                            return [np.nan] * len(q_cols)

                        n_rev = parse_n('매출액')
                        n_prof = parse_n('영업이익')
                        n_net = parse_n('당기순이익')

                        for l, r, p, n in zip(
                            n_lbls, n_rev, n_prof, n_net
                        ):
                            clean_l = l.replace('(E)', '').strip()
                            if np.isfinite(r) or np.isfinite(p):
                                q_dict[clean_l] = {
                                    "revenue": r,
                                    "profit": p,
                                    "net": n
                                }
                    break
        except Exception as e:
            logging.warning(f"Naver 재무제표 실패 {code}: {e}")

    if not q_dict:
        return fin_payload

    sorted_q = sorted(q_dict.keys())
    q_labels, q_rev, q_prof, q_net = [], [], [], []
    q_opm, q_prices, q_growth_yoy = [], [], []

    for idx, k in enumerate(sorted_q):
        r = q_dict[k]["revenue"]
        p = q_dict[k]["profit"]
        n = q_dict[k]["net"]

        q_labels.append(k)
        q_rev.append(r)
        q_prof.append(p)
        q_net.append(n)
        q_opm.append(round(p / r * 100, 1) if r > 0 else np.nan)
        q_prices.append(get_closest_price(k))

        # 4분기 전 같은 분기 대비. 데이터가 없으면 NaN.
        if idx >= 4:
            prev_p = q_dict[sorted_q[idx-4]]["profit"]
            yoy = (
                round((p - prev_p) / abs(prev_p) * 100, 1)
                if prev_p != 0 else np.nan
            )
        else:
            yoy = np.nan
        q_growth_yoy.append(yoy)

    fin_payload["quarterly"] = {
        "labels": q_labels,
        "profit": q_prof,
        "revenue": q_rev,
        "net": q_net,
        "opm": q_opm,
        "prices": q_prices,
        "growth_yoy": q_growth_yoy
    }

    # 반기/연간은 표시용 집계. 실제 성장률은 quarterly 원자료로 계산.
    fin_payload["semiannual"] = {
        "labels": q_labels[::2],
        "profit": q_prof[::2],
        "revenue": q_rev[::2],
        "net": q_net[::2],
        "opm": q_opm[::2],
        "prices": q_prices[::2],
        "growth_yoy": q_growth_yoy[::2]
    }
    fin_payload["annual"] = {
        "labels": [l[:4] + "년" for l in q_labels[::4]],
        "profit": q_prof[::4],
        "revenue": q_rev[::4],
        "net": q_net[::4],
        "opm": q_opm[::4],
        "prices": q_prices[::4],
        "growth_yoy": q_growth_yoy[::4]
    }

    quality = _growth_quality_and_reliability(q_dict, sorted_q)

    # Yahoo + Naver 교차 검증 가능 여부
    cross_verified = (
        per_info["source"].startswith("Yahoo")
        and np.isfinite(_safe_float(
            _fetch_naver_per(code).get("trailing_pe")
        ))
    )
    base_reliability = quality.get("_reliability_base", np.nan)
    source_score = 10 if cross_verified else (5 if np.isfinite(base_reliability) else np.nan)

    reliability = (
        min(100.0, float(base_reliability) + float(source_score))
        if np.isfinite(base_reliability) and np.isfinite(source_score)
        else np.nan
    )

    growth = quality.get("growth_quality", np.nan)
    per_value = per_info["per"]

    peg = (
        per_value / growth
        if np.isfinite(growth) and growth > 0 and np.isfinite(per_value)
        else np.nan
    )

    # PEG 1.0 / 0.5 목표 PER -> 목표주가.
    # 성장률이 양수이고 실제 PER이 존재할 때만 계산.
    target_per = growth if np.isfinite(growth) and growth > 0 else np.nan
    target_peg_10 = (
        int(cur_price * (target_per / per_value))
        if np.isfinite(cur_price) and np.isfinite(target_per)
        and per_value > 0
        else np.nan
    )
    target_peg_05 = (
        int(target_peg_10 * 0.5)
        if np.isfinite(target_peg_10)
        else np.nan
    )

    fin_payload["growth_model"].update({
        "est_per": per_value,
        "per_source": per_info["source"],
        "per_fallback": False,
        "growth_rate": growth,
        "growth_quality": growth,
        "peg": peg,
        "target_per": target_per,
        "target_peg_05": target_peg_05,
        "target_peg_10": target_peg_10,
        "peg_reliability": reliability,
        "revenue_growth": quality.get("revenue_growth", np.nan),
        "operating_growth": quality.get("operating_growth", np.nan),
        "opm_trend": quality.get("opm_trend", np.nan),
        "opm_latest": quality.get("opm_latest", np.nan),
        "profit_continuity": quality.get("profit_continuity", "데이터 부족"),
        "growth_consistency": quality.get("growth_consistency", np.nan),
        "alignment": quality.get("alignment", "데이터 부족"),
        "growth_samples": quality.get("growth_samples", 0)
    })

    return fin_payload


# ---------- v39.6 안전성 / 추세 / 수급 / 매수상태 ----------
def calculate_safety_metrics(df, real_dps, current_price):
    """배당수익률의 과거 위치를 안전성 필터로 변환."""
    if df.empty or current_price <= 0:
        return {
            "yield_5y_avg": np.nan, "yield_5y_max": np.nan,
            "yield_percentile": np.nan, "safety_score": np.nan,
            "safety_state": "데이터 부족"
        }

    y = (real_dps / df['Close'].replace(0, np.nan) * 100).dropna()
    y5 = y[y.index >= y.index.max() - pd.Timedelta(days=365*5)]
    if y5.empty:
        y5 = y

    current_yield = (
        real_dps / current_price * 100
        if np.isfinite(real_dps) and current_price > 0
        else np.nan
    )
    avg_y = float(y5.mean()) if not y5.empty else np.nan
    max_y = float(y5.max()) if not y5.empty else np.nan
    percentile = float((y5 <= current_yield).mean() * 100) if not y5.empty else np.nan

    # 현재 배당률이 높을수록 안전성 점수 상승.
    # 단순 최저가 판정이 아니라 과거 분포의 상위 구간을 반영.
    score = np.clip(percentile, 0, 100) if np.isfinite(percentile) else np.nan

    if np.isfinite(score):
        state = (
            "🟢 배당 안전마진 높음" if score >= 80
            else "🟡 배당 안전마진 보통" if score >= 55
            else "⚪ 배당 안전마진 낮음"
        )
    else:
        state = "데이터 부족"

    return {
        "yield_5y_avg": avg_y,
        "yield_5y_max": max_y,
        "yield_percentile": percentile,
        "safety_score": score,
        "safety_state": state
    }


def calculate_trend_metrics(df):
    """20/60/200일 추세와 모멘텀. 200거래일 미만이면 부족한 데이터는 제외."""
    out = {
        "sma20": np.nan, "sma60": np.nan, "sma200": np.nan,
        "pct_from_200": np.nan,
        "ma_alignment": "데이터 부족",
        "trend_score": np.nan,
        "golden_cross": False,
        "death_cross": False,
        "momentum_3m": np.nan,
        "trend_state": "데이터 부족"
    }
    if df.empty or 'Close' not in df.columns:
        return out

    close = pd.to_numeric(df['Close'], errors='coerce').dropna()
    if len(close) < 20:
        return out

    sma20 = close.rolling(20).mean()
    sma60 = close.rolling(60).mean()
    sma200 = close.rolling(200).mean()

    latest = float(close.iloc[-1])
    out["sma20"] = float(sma20.iloc[-1])
    out["sma60"] = float(sma60.iloc[-1]) if len(close) >= 60 else np.nan
    out["sma200"] = float(sma200.iloc[-1]) if len(close) >= 200 else np.nan

    if np.isfinite(out["sma200"]) and out["sma200"] > 0:
        out["pct_from_200"] = (latest / out["sma200"] - 1) * 100

    if len(close) >= 200:
        if out["sma20"] > out["sma60"] > out["sma200"]:
            out["ma_alignment"] = "20>60>200 정배열"
            alignment_score = 45
        elif out["sma20"] < out["sma60"] < out["sma200"]:
            out["ma_alignment"] = "20<60<200 역배열"
            alignment_score = 10
        elif out["sma20"] > out["sma200"]:
            out["ma_alignment"] = "200일선 상회"
            alignment_score = 35
        elif out["sma20"] < out["sma200"]:
            out["ma_alignment"] = "200일선 하회"
            alignment_score = 20
        else:
            alignment_score = 25

        # 200일선과의 거리를 과도하게 보상하지 않음.
        pct = out["pct_from_200"]
        distance_score = (
            10 if -5 <= pct <= 10
            else 15 if 10 < pct <= 20
            else 5 if -15 <= pct < -5
            else 0
        )

        # 최근 3개월 모멘텀
        lookback = min(60, len(close)-1)
        momentum = (latest / float(close.iloc[-1-lookback]) - 1) * 100
        out["momentum_3m"] = momentum
        momentum_score = (
            40 if momentum >= 15
            else 30 if momentum >= 5
            else 20 if momentum >= 0
            else 10 if momentum >= -10
            else 0
        )

        out["trend_score"] = float(
            np.clip(alignment_score + distance_score + momentum_score, 0, 100)
        )

        out["trend_state"] = (
            "🟢 상승 추세" if out["trend_score"] >= 70
            else "🟡 추세 확인" if out["trend_score"] >= 45
            else "🔴 하락/약세 추세"
        )

        # 골든/데드크로스 최근 신호
        gc = (sma20 > sma200) & (sma20.shift(1) <= sma200.shift(1))
        dc = (sma20 < sma200) & (sma20.shift(1) >= sma200.shift(1))
        out["golden_cross"] = bool(gc.iloc[-1]) if pd.notna(gc.iloc[-1]) else False
        out["death_cross"] = bool(dc.iloc[-1]) if pd.notna(dc.iloc[-1]) else False

    return out


def get_foreigner_institution_flow(code, days=90):
    """
    pykrx가 설치되어 있으면 외국인/기관 순매수 흐름을 가져온다.
    실패 시 빈 결과를 반환하며 점수에서 제외한다.
    """
    result = {
        "available": False,
        "foreign_5d": np.nan, "foreign_20d": np.nan, "foreign_60d": np.nan,
        "institution_5d": np.nan, "institution_20d": np.nan, "institution_60d": np.nan,
        "foreign_streak": 0,
        "flow_score": np.nan,
        "flow_state": "수급 데이터 없음",
        "source": "미확인"
    }
    try:
        from pykrx import stock
    except Exception:
        return result

    try:
        end = datetime.today().strftime("%Y%m%d")
        start = (datetime.today() - timedelta(days=days+30)).strftime("%Y%m%d")
        df_flow = stock.get_market_trading_value_by_date(start, end, code)

        if df_flow is None or df_flow.empty:
            return result

        df_flow = df_flow.sort_index()
        foreign_col = next(
            (c for c in df_flow.columns if '외국인' in str(c)),
            None
        )
        inst_col = next(
            (c for c in df_flow.columns if '기관' in str(c)),
            None
        )

        if foreign_col is None:
            return result

        result["available"] = True
        result["source"] = "pykrx"

        for n, key in [(5, "foreign_5d"), (20, "foreign_20d"), (60, "foreign_60d")]:
            if len(df_flow) >= n:
                result[key] = float(df_flow[foreign_col].tail(n).sum())

        if inst_col is not None:
            for n, key in [(5, "institution_5d"), (20, "institution_20d"), (60, "institution_60d")]:
                if len(df_flow) >= n:
                    result[key] = float(df_flow[inst_col].tail(n).sum())

        vals = pd.to_numeric(df_flow[foreign_col].tail(10), errors='coerce').fillna(0)
        streak = 0
        for v in reversed(vals.tolist()):
            if v > 0:
                streak += 1
            else:
                break
        result["foreign_streak"] = streak

        signs = []
        for k in ["foreign_5d", "foreign_20d", "foreign_60d"]:
            v = result[k]
            if np.isfinite(v):
                signs.append(1 if v > 0 else -1 if v < 0 else 0)

        if signs:
            result["flow_score"] = float(
                np.clip(
                    50 + 20*signs[0] + 15*(signs[1] if len(signs) > 1 else 0)
                    + 15*(signs[2] if len(signs) > 2 else 0),
                    0, 100
                )
            )
            result["flow_state"] = (
                "🟢 외국인 수급 개선" if result["flow_score"] >= 70
                else "🟡 수급 혼조" if result["flow_score"] >= 45
                else "🔴 외국인 수급 약세"
            )

        return result
    except Exception as e:
        logging.warning(f"pykrx 수급 실패 {code}: {e}")
        return result


def calculate_earnings_momentum(fin_data):
    """최근 실적에서 급등/연속상승 신호를 탐지."""
    q = fin_data.get("quarterly", {})
    profits = [
        float(x) for x in q.get("profit", [])
        if x is not None and np.isfinite(_safe_float(x))
    ]
    if len(profits) < 5:
        return {
            "signals": [],
            "score": 0,
            "state": "실적 모멘텀 데이터 부족"
        }

    signals = []
    latest, prev = profits[-1], profits[-2]

    if prev > 0 and latest / prev - 1 >= 0.30:
        signals.append("🚀 전분기 대비 영업익 30%↑")

    if profits[-5] > 0 and latest / profits[-5] - 1 >= 0.50:
        signals.append("📈 전년 동기 대비 50%↑")

    last5 = profits[-5:]
    if all(last5[i] > last5[i-1] for i in range(1, len(last5))):
        signals.append("📊 4분기 연속 영업익 상승")

    score = min(100, len(signals) * 33)
    state = (
        "💥 실적 모멘텀 강함" if score >= 66
        else "🟢 실적 개선" if score >= 33
        else "⚪ 실적 모멘텀 약함"
    )
    return {"signals": signals, "score": score, "state": state}


def determine_buy_state(gm, safety, trend, flow, earnings, current_price, buy_steps):
    """
    가치 → 안전성 → 추세 → 수급 순으로 계층적으로 판정.
    단순 100점 합산이 아니라 PEG를 1차 관문으로 사용한다.
    """
    peg = gm.get("peg", np.nan)
    rel = gm.get("peg_reliability", np.nan)
    gq = gm.get("growth_quality", np.nan)
    safety_score = safety.get("safety_score", np.nan)
    trend_score = trend.get("trend_score", np.nan)
    flow_score = flow.get("flow_score", np.nan)
    earnings_score = earnings.get("score", np.nan)

    def _price_text(v):
        try:
            x = float(v)
            return f"{x:,.0f}원" if np.isfinite(x) else "N/A"
        except Exception:
            return "N/A"

    if not np.isfinite(peg) or not np.isfinite(gq):
        state = "⚪ 관찰 — PEG 산출 데이터 부족"
        return {
            "state": state, "score": np.nan, "reason": "실제 성장률/이익 데이터 부족",
            "entry_hint": "재무 데이터가 확보될 때까지 PEG 기반 판단을 보류합니다."
        }

    # 가치점수: PEG가 낮을수록 높음
    value_score = (
        100 if peg <= 0.5 else
        85 if peg <= 0.75 else
        70 if peg <= 1.0 else
        50 if peg <= 1.25 else
        30 if peg <= 1.5 else 10
    )

    # 신뢰도가 낮으면 가치판정 강도를 제한
    if np.isfinite(rel):
        value_score *= (0.60 + 0.40 * rel / 100)

    # 계층별 확인 점수
    confirmation = []
    for v in [safety_score, trend_score, flow_score]:
        if np.isfinite(v):
            confirmation.append(float(v))
    confirmation_score = float(np.mean(confirmation)) if confirmation else np.nan

    score_parts = []
    if np.isfinite(value_score):
        score_parts.append((value_score, 0.55))
    if np.isfinite(confirmation_score):
        score_parts.append((confirmation_score, 0.25))
    if np.isfinite(earnings_score):
        score_parts.append((earnings_score, 0.20))

    if score_parts:
        final_score = float(np.clip(
            sum(v * w for v, w in score_parts) / sum(w for _, w in score_parts),
            0, 100
        ))
    else:
        final_score = np.nan

    # 최종 상태는 PEG/신뢰도를 우선 확인
    if peg <= 0.75 and (not np.isfinite(rel) or rel >= 70) and final_score >= 75:
        state = "🟢 적극 매수"
        entry_hint = f"현재가 {_price_text(current_price)}에서 1차 매수 {_price_text(buy_steps[0])} 기준으로 분할 진입을 고려."
    elif peg <= 1.0 and final_score >= 60:
        state = "🟡 분할 매수"
        entry_hint = f"1차 {_price_text(buy_steps[0])} → 2차 {_price_text(buy_steps[1])} 순서의 분할 진입."
    elif peg <= 1.0:
        state = "🔵 저평가 BUT 추세 대기"
        entry_hint = f"가치는 매력적이나 추세/수급 확인 후 2~3차 가격({_price_text(buy_steps[1])}~{_price_text(buy_steps[2])})을 우선."
    elif peg <= 1.5:
        state = "🟠 가격 부담"
        entry_hint = "성장 대비 가격 부담이 있어 배당 바닥선 또는 더 높은 성장 확인을 기다림."
    else:
        state = "🔴 매수 보류"
        entry_hint = "현재 PEG가 높아 가격 안전마진이 부족함."

    reasons = []
    reasons.append(f"PEG {peg:.2f}")
    if np.isfinite(rel):
        reasons.append(f"신뢰도 {rel:.0f}%")
    if np.isfinite(trend_score):
        reasons.append(f"추세 {trend_score:.0f}")
    if np.isfinite(flow_score):
        reasons.append(f"수급 {flow_score:.0f}")
    if earnings_score:
        reasons.append(f"실적모멘텀 {earnings_score:.0f}")

    return {
        "state": state,
        "score": final_score,
        "reason": " · ".join(reasons),
        "entry_hint": entry_hint
    }

# ---------- 동적 가중치 ----------
def calculate_dynamic_weights(current_yield, growth_rate):
    if not np.isfinite(current_yield) or not np.isfinite(growth_rate):
        return np.nan, np.nan, "데이터 부족"

    w_div = 0.5
    w_growth = 0.5
    profile_desc = "균형 성장/배당 믹스형"

    if current_yield >= 5.0 and growth_rate < 10.0:
        w_div = 0.8
        w_growth = 0.2
        profile_desc = "고배당 안정형 체질"
    elif current_yield < 1.5 and growth_rate >= 20.0:
        w_div = 0.2
        w_growth = 0.8
        profile_desc = "고성장 모멘텀형 체질"
    elif current_yield >= 3.0 and growth_rate >= 15.0:
        w_div = 0.4
        w_growth = 0.6
        profile_desc = "배당성장 복합 체질"

    return w_div, w_growth, profile_desc



# ---------- v39.6 다중 기간 연산 & 배당/PEG/추세/수급 통합 엔진 ----------
def calculate_multi_period_engine(code, name):
    now = datetime.today()
    start_date = (now - timedelta(days=365 * 11)).strftime('%Y-%m-%d')

    df = get_price_data(code, name, start_date)
    if df.empty:
        raise ValueError(
            "가격 데이터를 가져올 수 없습니다. 네트워크 상태를 확인하거나 종목 코드를 확인해주세요."
        )
    if len(df) < 5:
        raise ValueError("충분한 가격 데이터를 확보하지 못했습니다.")

    latest_price = int(df['Close'].iloc[-1])
    prev_price = int(df['Close'].iloc[-2])
    change_pct = ((latest_price - prev_price) / prev_price) * 100

    real_dps = get_dps_automatically(code, name)

    # 2주 샘플링으로 장기 배당밴드 계산
    df_all = df.resample('2W').last().dropna()
    df_all['DPS_TTM'] = real_dps
    df_all['Yield'] = (
        df_all['DPS_TTM'] / df_all['Close'].replace(0, np.nan) * 100
    )

    current_yield = (
        float(real_dps / latest_price * 100)
        if np.isfinite(real_dps) and latest_price > 0
        else np.nan
    )

    # RSI
    close_all = df_all['Close']
    delta = close_all.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df_all['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

    periods_def = {
        '1Y': ('1년 (단기 바닥)', 1, '1차 매수'),
        '3Y': ('3년 (중기 바닥)', 3, '2차 매수'),
        '5Y': ('5년 (장기 안전마진)', 5, '3차 매수'),
        '10Y': ('10년 (역사적 대바닥)', 10, '풀매수')
    }

    matrix_table = []
    period_stats = {}
    for key, (label, yr, alloc) in periods_def.items():
        sub_df = df_all[df_all.index >= now - timedelta(days=365 * yr)]
        if sub_df.empty:
            sub_df = df_all

        p_max_yield = float(sub_df['Yield'].max()) if not sub_df.empty else np.nan
        floor_price = (
            int(real_dps / (p_max_yield / 100))
            if np.isfinite(p_max_yield) and p_max_yield > 0
            else np.nan
        )
        gap = (
            ((latest_price - floor_price) / floor_price) * 100
            if np.isfinite(floor_price) and floor_price > 0
            else np.nan
        )

        buy_possible = np.isfinite(floor_price) and latest_price <= floor_price
        matrix_table.append({
            "key": key,
            "period": label,
            "allocation": alloc,
            "max_yield": p_max_yield,
            "floor_price": floor_price,
            "gap": gap,
            "diff_won": latest_price - floor_price if np.isfinite(floor_price) else np.nan,
            "status": "🎯 매수 가능" if buy_possible else "⏳ 대기 (비쌈)",
            "badge": (
                "bg-red-950 text-red-400 font-bold"
                if buy_possible
                else "bg-slate-800 text-slate-400"
            )
        })
        period_stats[key] = {
            "max_yield": p_max_yield,
            "floor_price": floor_price
        }

    fin_data = get_pure_real_fundamentals(code, name, df)
    gm = fin_data['growth_model']

    div_1y = matrix_table[0]['floor_price']
    div_3y = matrix_table[1]['floor_price']
    div_5y = matrix_table[2]['floor_price']

    peg_fair = gm.get('target_peg_10', np.nan)
    peg_bottom = gm.get('target_peg_05', np.nan)
    growth_rate = gm.get('growth_rate', np.nan)

    # 안전성/추세/수급/실적모멘텀
    safety = calculate_safety_metrics(df, real_dps, latest_price)
    trend = calculate_trend_metrics(df)
    flow = get_foreigner_institution_flow(code)
    earnings = calculate_earnings_momentum(fin_data)

    # 성장 데이터가 없으면 가중치도 NULL. 임의로 배당/성장 비중을 정하지 않는다.
    if np.isfinite(growth_rate) and np.isfinite(current_yield):
        w_div, w_growth, profile_desc = calculate_dynamic_weights(
            current_yield, growth_rate
        )
    else:
        w_div, w_growth = np.nan, np.nan
        profile_desc = "데이터 부족"

    # PEG 데이터가 없으면 PEG 목표가도 NULL. 배당가격을 PEG의 대체값으로 사용하지 않는다.
    peg_fair_value = peg_fair if np.isfinite(peg_fair) else np.nan
    peg_bottom_value = peg_bottom if np.isfinite(peg_bottom) else np.nan

    def blend(a, b, wa, wb):
        vals, weights = [], []
        if np.isfinite(a):
            vals.append(float(a)); weights.append(wa)
        if np.isfinite(b):
            vals.append(float(b)); weights.append(wb)
        if not vals:
            return np.nan
        return int(np.average(vals, weights=weights))

    if np.isfinite(w_div) and np.isfinite(w_growth):
        buy_step_1 = blend(div_1y, peg_fair_value, w_div, w_growth)
        buy_step_2 = blend(
            div_3y,
            (peg_fair_value * 0.6 + peg_bottom_value * 0.4)
            if np.isfinite(peg_fair_value) and np.isfinite(peg_bottom_value)
            else np.nan,
            w_div, w_growth
        )
        buy_step_3 = blend(div_5y, peg_bottom_value, w_div, w_growth)
    else:
        # PEG/Growth 데이터가 없으면 혼합가격을 만들지 않는다.
        # 배당 데이터 자체가 존재할 때의 독립적인 배당 밴드 가격만 사용한다.
        buy_step_1 = div_1y
        buy_step_2 = div_3y
        buy_step_3 = div_5y

    buy_state = determine_buy_state(
        gm=gm,
        safety=safety,
        trend=trend,
        flow=flow,
        earnings=earnings,
        current_price=latest_price,
        buy_steps=(buy_step_1, buy_step_2, buy_step_3)
    )

    chart_payload = {
        "dates": df_all.index.strftime('%y.%m.%d').tolist(),
        "prices": df_all['Close'].astype(int).tolist(),
        "yields": [round(float(v), 2) for v in df_all['Yield']],
        "rsis": [round(float(v), 1) for v in df_all['RSI']],
        "dps": df_all['DPS_TTM'].tolist(),
        "stats": period_stats
    }

    return {
        "code": code,
        "name": name,
        "latest_price": latest_price,
        "change_pct": change_pct,
        "current_yield": current_yield,
        "current_dps": real_dps,
        "matrix": matrix_table,
        "buy_step_1": int(buy_step_1) if np.isfinite(buy_step_1) else np.nan,
        "buy_step_2": int(buy_step_2) if np.isfinite(buy_step_2) else np.nan,
        "buy_step_3": int(buy_step_3) if np.isfinite(buy_step_3) else np.nan,
        "div_1y": div_1y,
        "div_5y": div_5y,
        "peg_fair": peg_fair,
        "peg_bottom": peg_bottom,
        "w_div": (int(w_div * 100) if np.isfinite(w_div) else np.nan),
        "w_growth": (int(w_growth * 100) if np.isfinite(w_growth) else np.nan),
        "profile_desc": profile_desc,
        "fin_data": fin_data,
        "safety": safety,
        "trend": trend,
        "flow": flow,
        "earnings": earnings,
        "buy_state": buy_state,
        "chart_payload": chart_payload
    }

# ---------- GUI 렌더링 함수 (HTML 문자열 반환) ----------
def generate_v39_dashboard(query, code=None, name=None):
    if code is None or name is None:
        code, name = get_code_and_name(query)
    if not code:
        return None

    data = calculate_multi_period_engine(code, name)
    if not data:
        return None

    news_items, notice_items = get_news_and_disclosures(code)
    fin = data['fin_data']
    gm = fin.get('growth_model', {})
    file_name = f"dividend_dashboard_{code}.html"

    b1 = data['buy_step_1']
    b2 = data['buy_step_2']
    b3 = data['buy_step_3']
    div_1y = data['div_1y']
    div_5y = data['div_5y']
    peg_fair = data['peg_fair']
    peg_bottom = data['peg_bottom']
    w_div = data['w_div']
    w_growth = data['w_growth']
    profile_desc = data['profile_desc']

    # v39.6 통합 분석 지표
    safety = data.get('safety', {})
    trend = data.get('trend', {})
    flow = data.get('flow', {})
    earnings = data.get('earnings', {})
    buy_state = data.get('buy_state', {})
    peg_rel = gm.get('peg_reliability', np.nan)
    growth_quality = gm.get('growth_quality', np.nan)
    per_value = gm.get('est_per', np.nan)
    per_source = gm.get('per_source', '데이터 없음')
    revenue_growth = gm.get('revenue_growth', np.nan)
    operating_growth = gm.get('operating_growth', np.nan)
    opm_trend = gm.get('opm_trend', np.nan)
    trend_score = trend.get('trend_score', np.nan)
    pct_from_200 = trend.get('pct_from_200', np.nan)
    flow_score = flow.get('flow_score', np.nan)
    foreign_streak = flow.get('foreign_streak', 0)
    safety_score = safety.get('safety_score', np.nan)
    earnings_score = earnings.get('score', 0)

    def fmt_num(v, digits=1, suffix=''):
        try:
            return f"{float(v):.{digits}f}{suffix}" if np.isfinite(float(v)) else 'N/A'
        except Exception:
            return 'N/A'

    def fmt_price(v):
        try:
            x = float(v)
            return f"{x:,.0f}원" if np.isfinite(x) else 'N/A'
        except Exception:
            return 'N/A'

    def fmt_pct(v, digits=2):
        return fmt_num(v, digits, '%')

    # IMPORTANT: 중첩 f-string + JavaScript 중괄호로 인한 SyntaxError 방지
    matrix_rows = "".join(
        f"""
        <tr onclick="changePeriod('{m['key']}')" class="hover:bg-slate-800/60 transition">
            <td class="py-2.5 px-2.5 font-bold text-slate-200">{m['period']}</td>
            <td class="py-2.5 px-2.5 text-cyan-300">{m['allocation']}</td>
            <td class="py-2.5 px-2.5 text-blue-400 font-bold">{fmt_pct(m['max_yield'], 2)}</td>
            <td class="py-2.5 px-2.5 text-red-400 font-black text-sm">{fmt_price(m['floor_price'])}</td>
            <td class="py-2.5 px-2.5 {('text-red-400 font-bold' if m['gap'] <= 0 else ('text-amber-400' if m['gap'] <= 3 else 'text-slate-300'))}">
                {fmt_price(m['diff_won'])} ({fmt_pct(m['gap'], 1)})
            </td>
            <td class="py-2.5 px-2.5 text-center">
                <span class="px-2 py-0.5 text-[10px] rounded {m['badge']}">{m['status']}</span>
            </td>
        </tr>
        """
        for m in data['matrix']
    )

    news_html = "".join(
        f"""
        <a href="{n['link']}" target="_blank" class="block p-3 bg-slate-950/60 hover:bg-slate-950 rounded-xl border border-slate-800/80 transition">
            <div class="flex items-center justify-between gap-1 mb-1.5"><span class="px-2 py-0.5 text-[9px] font-bold rounded bg-blue-950 text-blue-300 border border-blue-800">{n['tag']}</span><span class="text-[10px] text-slate-400">{n['press']} · {n['date']}</span></div>
            <p class="text-xs text-slate-200 font-medium hover:text-blue-300 leading-snug line-clamp-2">{n['title']}</p>
        </a>
        """
        for n in news_items
    ) if news_items else '<p class="text-xs text-slate-400 text-center py-16">뉴스가 없습니다.</p>'

    notice_html = "".join(
        f"""
        <a href="{n['link']}" target="_blank" class="block p-3 bg-slate-950/60 hover:bg-slate-950 rounded-xl border border-slate-800/80 transition">
            <div class="flex items-center justify-between gap-1 mb-1.5"><span class="px-2 py-0.5 text-[9px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">{n['tag']}</span><span class="text-[10px] text-slate-400">{n['press']} · {n['date']}</span></div>
            <p class="text-xs text-slate-200 font-medium hover:text-amber-300 leading-snug line-clamp-2">{n['title']}</p>
        </a>
        """
        for n in notice_items
    ) if notice_items else '<p class="text-xs text-slate-400 text-center py-16">공시가 없습니다.</p>'

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[{code}] {data['name']} 종목 맞춤형 동적 가중치 대시보드</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700;800&display=swap');
        body {{ font-family: 'Pretendard', sans-serif; background-color: #0b0f19; color: #f1f5f9; }}
        .custom-scroll::-webkit-scrollbar {{ width: 5px; }}
        .custom-scroll::-webkit-scrollbar-track {{ background: #111827; }}
        .custom-scroll::-webkit-scrollbar-thumb {{ background: #374151; border-radius: 3px; }}
    </style>
</head>
<body class="p-3 md:p-6 custom-scroll">
    <div class="max-w-6xl mx-auto space-y-4">
        
        <!-- 상단 헤더 -->
        <div class="bg-slate-900/90 p-5 rounded-2xl border border-slate-800 shadow-2xl backdrop-blur-md flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
                <div class="flex items-center gap-3">
                    <h1 class="text-2xl font-extrabold text-white tracking-tight">{data['name']}</h1>
                    <span class="text-xs px-2.5 py-1 bg-slate-800 text-blue-400 font-mono rounded-lg border border-slate-700">{code}</span>
                    <span class="text-xs px-3 py-1 bg-indigo-950 text-indigo-300 font-bold rounded-lg border border-indigo-800">
                        {profile_desc} (배당 {fmt_num(w_div, 0, '%')} : 성장 {fmt_num(w_growth, 0, '%')})
                    </span>
                </div>
                <p class="text-xs text-slate-400 mt-2">
                    현재 주가: <b class="text-white text-base font-extrabold">{data['latest_price']:,}원</b> ({data['change_pct']:+.2f}%) 
                    · 현재 배당수익률: <b class="text-blue-400 text-base font-extrabold">{fmt_pct(data['current_yield'], 2)}</b> (연간 실시간 DPS {fmt_price(data['current_dps'])})
                </p>
            </div>
            <div>
                <a href="https://finance.naver.com/item/main.naver?code={code}" target="_blank" 
                   class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-2 rounded-xl border border-slate-700 transition inline-block">
                    네이버 증권 열기 ↗
                </a>
            </div>
        </div>

        <!-- [최상단 배치] 종목 맞춤형 동적 가중치 3단계 매수 전략 마스터 결론 카드 -->
        <div class="p-5 rounded-2xl border shadow-2xl bg-gradient-to-r from-slate-900 via-indigo-950/80 to-slate-900 border-indigo-500/50 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                <div class="flex items-center gap-2">
                    <span class="px-2.5 py-0.5 text-xs font-black rounded-lg bg-indigo-600 text-white">마스터 매매 결론</span>
                    <h2 class="text-base md:text-lg font-black text-indigo-200 tracking-tight">종목 맞춤형 동적 가중치 3단계 매수가이드</h2>
                </div>
                <span class="text-xs text-slate-400">실데이터 기반 · 배당 {fmt_num(w_div, 0, '%')} + 실적 PEG {fmt_num(w_growth, 0, '%')} + 안전성/추세/수급 통합</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 pt-1">
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-blue-400 font-bold">1차 매수 (30% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{fmt_price(b1)}</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 1년 바닥 + PEG 적정가 ({fmt_num(w_div, 0)}:{fmt_num(w_growth, 0)})</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-emerald-400 font-bold">2차 매수 (30% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{fmt_price(b2)}</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 3년 바닥 + PEG 중간 ({fmt_num(w_div, 0)}:{fmt_num(w_growth, 0)})</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-red-400 font-bold">3차 매수 (40% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{fmt_price(b3)}</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 5년 대바닥 + PEG 바닥가 ({fmt_num(w_div, 0)}:{fmt_num(w_growth, 0)})</p>
                </div>
            </div>
        </div>

        <!-- v39.6 가치/안전성/추세/수급 통합 판단 -->
        <div class="bg-slate-900/90 p-4 rounded-2xl border border-slate-800 shadow-xl space-y-3">
            <div class="flex flex-col md:flex-row md:items-center justify-between gap-2 border-b border-slate-800 pb-3">
                <div>
                    <p class="text-[11px] text-slate-400 font-bold">v39.6 계층형 투자 판단</p>
                    <h2 class="text-xl font-black text-white mt-0.5">{buy_state.get('state', '관찰')}</h2>
                    <p class="text-[11px] text-slate-400 mt-1">{buy_state.get('reason', '')}</p>
                </div>
                <div class="text-right">
                    <p class="text-[10px] text-slate-500">종합 확인점수</p>
                    <p class="text-2xl font-black text-indigo-300">{fmt_num(buy_state.get('score'), 0, '/100')}</p>
                </div>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-[10px] text-slate-500">가치 · PEG</p>
                    <p class="text-lg font-black text-emerald-400">{fmt_num(gm.get('peg'), 2)}</p>
                    <p class="text-[9px] text-slate-500">PER {fmt_num(per_value, 1)}배</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-[10px] text-slate-500">PEG 신뢰도</p>
                    <p class="text-lg font-black text-cyan-400">{fmt_num(peg_rel, 0, '%')}</p>
                    <p class="text-[9px] text-slate-500 truncate">{per_source}</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-[10px] text-slate-500">Growth Quality</p>
                    <p class="text-lg font-black text-blue-400">{fmt_num(growth_quality, 1, '%')}</p>
                    <p class="text-[9px] text-slate-500">매출 {fmt_num(revenue_growth, 1, '%')} · 영업익 {fmt_num(operating_growth, 1, '%')}</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-[10px] text-slate-500">추세</p>
                    <p class="text-lg font-black text-amber-400">{fmt_num(trend_score, 0, '/100')}</p>
                    <p class="text-[9px] text-slate-500">200일선 대비 {fmt_num(pct_from_200, 1, '%')}</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-[10px] text-slate-500">안전성 · 수급</p>
                    <p class="text-lg font-black text-violet-400">{fmt_num(safety_score, 0, '/100')}</p>
                    <p class="text-[9px] text-slate-500">수급 {fmt_num(flow_score, 0, '/100')} · 외인 {foreign_streak}일</p>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px]">
                <div class="bg-slate-950/50 rounded-lg px-3 py-2 text-slate-300">
                    <b class="text-emerald-400">실적 모멘텀</b> · {earnings.get('state', '데이터 부족')}
                    <span class="text-slate-500"> ({earnings_score:.0f}/100)</span>
                </div>
                <div class="bg-slate-950/50 rounded-lg px-3 py-2 text-slate-300">
                    <b class="text-amber-400">OPM 추세</b> · {fmt_num(opm_trend, 1, '%p')}
                </div>
                <div class="bg-slate-950/50 rounded-lg px-3 py-2 text-slate-300">
                    <b class="text-blue-400">매수 힌트</b> · {buy_state.get('entry_hint', '')}
                </div>
            </div>
        </div>

        <!-- 대형 뷰 전환 스위처 -->
        <div class="flex items-center gap-2 bg-slate-900/90 p-1.5 rounded-2xl border border-slate-800">
            <button id="viewDivBtn" onclick="switchMainView('div')" class="flex-1 py-2.5 rounded-xl text-xs font-black bg-blue-600 text-white shadow-lg transition flex items-center justify-center gap-2">
                <span>💰</span> 배당 가치 분석 뷰 (주가 · 역축 배당률 · 바닥선)
            </button>
            <button id="viewGrowthBtn" onclick="switchMainView('growth')" class="flex-1 py-2.5 rounded-xl text-xs font-black bg-slate-800 text-slate-400 hover:text-white transition flex items-center justify-center gap-2">
                <span>🚀</span> 실적 성장 분석 뷰 (PEG · 실적동행 차트)
            </button>
        </div>

        <!-- 메인 레이아웃 -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
            
            <div class="lg:col-span-2 space-y-4">
                
                <!-- [VIEW 1] 배당 가치 분석 뷰 -->
                <div id="sectionDividendView" class="space-y-4">
                    <div class="bg-slate-900/90 p-4 rounded-xl border border-blue-500/40 space-y-1">
                        <p class="text-xs text-blue-400 font-bold">💰 배당 성장 기반 단독 매수 전략 (실시간 자동 DPS 반영)</p>
                        <h3 class="text-xl font-black text-white">{div_1y:,}원 <span class="text-xs font-normal text-slate-400">/ 5년 대바닥 {div_5y:,}원</span></h3>
                        <p class="text-[11px] text-slate-300 pt-1">실시간 크롤링된 DPS 기준 1년 바닥 <b>30% 매수</b>, 5년 대바닥 <b>40% 적극 매수</b></p>
                    </div>

                    <div class="bg-slate-900/80 p-4 rounded-2xl border border-slate-800 space-y-2.5">
                        <div class="flex items-center justify-between pb-1.5 border-b border-slate-800">
                            <h3 class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                                <span>📊</span> 기간별 배당 바닥선 (최고 배당률 도달 주가)
                            </h3>
                            <span class="text-[11px] text-slate-400">행 클릭 시 차트 기간 연동</span>
                        </div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-xs text-left">
                                <thead class="text-slate-400 bg-slate-950/60 uppercase border-b border-slate-800">
                                    <tr>
                                        <th class="py-2 px-2.5">기간</th>
                                        <th class="py-2 px-2.5">비중</th>
                                        <th class="py-2 px-2.5 text-blue-400 font-bold">역대 최고 배당률</th>
                                        <th class="py-2 px-2.5 text-red-400 font-bold">도달 시 바닥 주가</th>
                                        <th class="py-2 px-2.5">현재가와 괴리율</th>
                                        <th class="py-2 px-2.5 text-center">매수 판정</th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y divide-slate-800/60 font-medium cursor-pointer">
                                    {matrix_rows}
                    </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-800 space-y-3">
                        <div class="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-slate-800 text-xs">
                            <div class="flex items-center bg-slate-950 p-1 rounded-xl border border-slate-800 gap-1">
                                <button id="btn1Y" onclick="changePeriod('1Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">1년</button>
                                <button id="btn3Y" onclick="changePeriod('3Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">3년</button>
                                <button id="btn5Y" onclick="changePeriod('5Y')" class="px-2.5 py-1 rounded-lg text-xs font-bold bg-blue-600 text-white transition">5년</button>
                                <button id="btn10Y" onclick="changePeriod('10Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">10년</button>
                            </div>
                            <div class="flex flex-wrap items-center gap-3">
                                <label class="flex items-center gap-1 cursor-pointer text-white font-bold"><input type="checkbox" id="chkPrice" checked onchange="toggleLayers()"> 주가</label>
                                <label class="flex items-center gap-1 cursor-pointer text-blue-400 font-bold"><input type="checkbox" id="chkYield" checked onchange="toggleLayers()"> 배당수익률(역축)</label>
                                <label class="flex items-center gap-1 cursor-pointer text-red-400 font-bold"><input type="checkbox" id="chkFloor" checked onchange="toggleLayers()"> 바닥선</label>
                                <label class="flex items-center gap-1 cursor-pointer text-emerald-400 font-bold"><input type="checkbox" id="chkSniper" checked onchange="toggleLayers()"> 🟢저점신호</label>
                            </div>
                        </div>
                        <div id="chartHud" class="bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                            <div class="flex items-center gap-1.5 font-mono text-slate-400 font-bold"><span>📅</span> <span id="hudDate">-</span></div>
                            <div class="flex flex-wrap items-center gap-3 font-semibold text-[11px]">
                                <span class="text-white">주가: <b id="hudPrice" class="font-bold text-white text-xs">-</b></span>
                                <span class="text-blue-400">배당률: <b id="hudYield">-</b></span>
                                <span class="text-red-400"><span id="hudFloorLabel">5년</span>바닥가: <b id="hudFloor">-</b></span>
                            </div>
                        </div>
                        <div class="relative h-[290px] w-full"><canvas id="mainChart"></canvas></div>
                    </div>

                    <div class="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                        <div class="flex items-center justify-between pb-1 border-b border-slate-800 text-xs">
                            <span class="font-semibold text-slate-300">RSI 과매도 지표 (14)</span>
                            <span id="rsiHud" class="text-[11px] text-cyan-400 font-bold">RSI: -</span>
                        </div>
                        <div class="relative h-[80px] w-full mt-1"><canvas id="rsiChart"></canvas></div>
                    </div>
                </div>

                <!-- [VIEW 2] 실적 성장 분석 뷰 -->
                <div id="sectionGrowthView" class="space-y-4 hidden">
                    <div class="bg-slate-900/90 p-4 rounded-xl border border-emerald-500/40 space-y-1">
                        <p class="text-xs text-emerald-400 font-bold">🚀 실적 성장(PEG) 기반 단독 매수 전략 (실데이터 연동)</p>
                        <h3 class="text-xl font-black text-white">{peg_fair:,}원 <span class="text-xs font-normal text-slate-400">/ 바닥 {peg_bottom:,}원</span></h3>
                        <p class="text-[11px] text-slate-300 pt-1">PEG 1.0배 적정가에서 <b>50% 분할 매수</b>, PEG 0.5배 바닥가에서 <b>50% 적극 매수</b></p>
                    </div>

                    <div class="bg-slate-900/90 p-4 rounded-2xl border border-slate-800 space-y-3">
                        <div class="flex flex-wrap items-center justify-between gap-3 text-xs">
                            <div class="flex items-center gap-1.5">
                                <span class="text-[11px] font-bold text-slate-400">집계 단위:</span>
                                <div class="flex items-center bg-slate-950 p-1 rounded-xl border border-slate-800 gap-1">
                                    <button id="btnFreqQ" onclick="changeFinFreq('quarterly')" class="px-2.5 py-1 rounded-lg text-xs font-bold bg-emerald-600 text-white transition">분기</button>
                                    <button id="btnFreqS" onclick="changeFinFreq('semiannual')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">반기</button>
                                    <button id="btnFreqA" onclick="changeFinFreq('annual')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">1년 (연간)</button>
                                </div>
                            </div>
                            <div class="flex items-center gap-1.5">
                                <span class="text-[11px] font-bold text-slate-400">조회 기간:</span>
                                <div class="flex items-center bg-slate-950 p-1 rounded-xl border border-slate-800 gap-1">
                                    <button id="btnFin1Y" onclick="changeFinPeriod('1Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">1년</button>
                                    <button id="btnFin3Y" onclick="changeFinPeriod('3Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">3년</button>
                                    <button id="btnFin5Y" onclick="changeFinPeriod('5Y')" class="px-2.5 py-1 rounded-lg text-xs font-bold bg-blue-600 text-white transition">5년</button>
                                    <button id="btnFin10Y" onclick="changeFinPeriod('10Y')" class="px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition">10년(전체)</button>
                                </div>
                            </div>
                            <div class="flex flex-wrap items-center gap-2 text-[11px]">
                                <label class="flex items-center gap-1 cursor-pointer text-white font-bold"><input type="checkbox" id="chkGrowthPrice" checked onchange="toggleGrowthLayers()"> 주가</label>
                                <label class="flex items-center gap-1 cursor-pointer text-emerald-400 font-bold"><input type="checkbox" id="chkGrowthProf" checked onchange="toggleGrowthLayers()"> 영업익</label>
                                <label class="flex items-center gap-1 cursor-pointer text-cyan-400 font-bold"><input type="checkbox" id="chkGrowthYoY" checked onchange="toggleGrowthLayers()"> 성장률(YoY)</label>
                                <label class="flex items-center gap-1 cursor-pointer text-amber-400 font-bold"><input type="checkbox" id="chkGrowthOpm" checked onchange="toggleGrowthLayers()"> OPM</label>
                            </div>
                        </div>
                    </div>

                    <div class="grid grid-cols-2 md:grid-cols-4 gap-2.5">
                        <div class="bg-slate-900/90 p-3.5 rounded-xl border border-slate-800">
                            <p class="text-[11px] text-slate-400">🚀 영업이익 기간 증감</p>
                            <h4 id="cardProfGrowth" class="text-lg font-bold text-emerald-400 mt-0.5">-</h4>
                            <span id="cardProfSub" class="text-[10px] text-slate-400">선택 기간 시작 대비</span>
                        </div>
                        <div class="bg-slate-900/90 p-3.5 rounded-xl border border-slate-800">
                            <p class="text-[11px] text-slate-400">📈 OPM 마진 변동폭</p>
                            <h4 id="cardOpm" class="text-lg font-bold text-amber-400 mt-0.5">-</h4>
                            <span id="cardOpmSub" class="text-[10px] text-slate-400">시작 ➔ 최근 (평균 OPM)</span>
                        </div>
                        <div class="bg-slate-900/90 p-3.5 rounded-xl border border-slate-800">
                            <p class="text-[11px] text-slate-400">🏢 매출액 기간 증감</p>
                            <h4 id="cardRevGrowth" class="text-lg font-bold text-slate-200 mt-0.5">-</h4>
                            <span id="cardRevSub" class="text-[10px] text-slate-400">외형 확장성</span>
                        </div>
                        <div class="bg-slate-900/90 p-3.5 rounded-xl border border-slate-800">
                            <p class="text-[11px] text-slate-400">🛡️ 기간 누적 당기순익</p>
                            <h4 id="cardNet" class="text-lg font-bold text-white mt-0.5">-</h4>
                            <span id="cardNetSub" class="text-[10px] text-emerald-400">배당 안전성</span>
                        </div>
                    </div>

                    <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-800 space-y-3">
                        <div id="growthChartHud" class="bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 flex flex-wrap items-center justify-between gap-2 text-xs">
                            <div class="flex items-center gap-1.5 font-mono text-slate-300 font-bold"><span>📅</span> <span id="gHudDate">-</span></div>
                            <div class="flex flex-wrap items-center gap-3 font-semibold text-[11px]">
                                <span class="text-white">주가: <b id="gHudPrice" class="font-bold text-white text-xs">-</b></span>
                                <span class="text-emerald-400">영업익: <b id="gHudProf">-</b></span>
                                <span class="text-cyan-400">YoY성장률: <b id="gHudYoY">-</b></span>
                                <span class="text-amber-400">OPM: <b id="gHudOpm">-</b></span>
                            </div>
                        </div>
                        <div class="flex items-center justify-between text-xs text-slate-300 pt-1">
                            <span id="growthChartTitle" class="font-bold text-slate-200">실적 & 주가 동행 차트</span>
                            <span class="text-[11px] text-slate-400">막대: 영업익 · 흰색선: 주가 · 하늘선: YoY · 노란선: OPM</span>
                        </div>
                        <div class="relative h-[360px] w-full"><canvas id="growthChart"></canvas></div>
                    </div>
                </div>

            </div>

            <!-- 우측 뉴스/공시 -->
            <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-800 flex flex-col h-[540px]">
                <div class="flex items-center gap-2 border-b border-slate-800 pb-2.5">
                    <button id="tabNewsBtn" onclick="switchTab('news')" class="flex-1 py-1.5 text-xs font-bold rounded-lg bg-blue-600 text-white transition">📰 뉴스</button>
                    <button id="tabNoticeBtn" onclick="switchTab('notice')" class="flex-1 py-1.5 text-xs font-bold rounded-lg bg-slate-800 text-slate-400 hover:text-slate-200 transition">📑 공시</button>
                </div>
                <div id="feedNews" class="flex-1 overflow-y-auto space-y-2 pt-2.5 pr-1 custom-scroll">
                    {news_html}
                </div>
                <div id="feedNotice" class="flex-1 overflow-y-auto space-y-2 pt-2.5 pr-1 custom-scroll hidden">
                    {notice_html}
                </div>
            </div>

        </div>

    </div>

    <script>
        function switchMainView(mode) {{
            const btnDiv = document.getElementById('viewDivBtn');
            const btnGrowth = document.getElementById('viewGrowthBtn');
            const secDiv = document.getElementById('sectionDividendView');
            const secGrowth = document.getElementById('sectionGrowthView');

            if (mode === 'div') {{
                btnDiv.className = "flex-1 py-2.5 rounded-xl text-xs font-black bg-blue-600 text-white shadow-lg transition flex items-center justify-center gap-2";
                btnGrowth.className = "flex-1 py-2.5 rounded-xl text-xs font-black bg-slate-800 text-slate-400 hover:text-white transition flex items-center justify-center gap-2";
                secDiv.classList.remove('hidden');
                secGrowth.classList.add('hidden');
            }} else {{
                btnGrowth.className = "flex-1 py-2.5 rounded-xl text-xs font-black bg-blue-600 text-white shadow-lg transition flex items-center justify-center gap-2";
                btnDiv.className = "flex-1 py-2.5 rounded-xl text-xs font-black bg-slate-800 text-slate-400 hover:text-white transition flex items-center justify-center gap-2";
                secGrowth.classList.remove('hidden');
                secDiv.classList.add('hidden');
                updateGrowthChart();
            }}
        }}

        function switchTab(type) {{
            const tabNewsBtn = document.getElementById('tabNewsBtn');
            const tabNoticeBtn = document.getElementById('tabNoticeBtn');
            const feedNews = document.getElementById('feedNews');
            const feedNotice = document.getElementById('feedNotice');
            if (type === 'news') {{
                tabNewsBtn.className = "flex-1 py-1.5 text-xs font-bold rounded-lg bg-blue-600 text-white transition";
                tabNoticeBtn.className = "flex-1 py-1.5 text-xs font-bold rounded-lg bg-slate-800 text-slate-400 hover:text-slate-200 transition";
                feedNews.classList.remove('hidden'); feedNotice.classList.add('hidden');
            }} else {{
                tabNoticeBtn.className = "flex-1 py-1.5 text-xs font-bold rounded-lg bg-blue-600 text-white transition";
                tabNewsBtn.className = "flex-1 py-1.5 text-xs font-bold rounded-lg bg-slate-800 text-slate-400 hover:text-slate-200 transition";
                feedNotice.classList.remove('hidden'); feedNews.classList.add('hidden');
            }}
        }}

        // 배당 차트 엔진
        const rawData = {json.dumps(data['chart_payload'])};
        let currentPeriod = '5Y';
        const periodPoints = {{ '1Y': 26, '3Y': 78, '5Y': 130, '10Y': rawData.dates.length }};
        let activeDates = [], activePrices = [], activeYields = [], activeFloors = [], activeSnipers = [], activeRsis = [];

        function sliceDataForPeriod(periodKey) {{
            let pts = periodPoints[periodKey];
            if (!pts || pts > rawData.dates.length) pts = rawData.dates.length;
            const startIdx = Math.max(0, rawData.dates.length - pts);
            activeDates = rawData.dates.slice(startIdx);
            activePrices = rawData.prices.slice(startIdx);
            activeYields = rawData.yields.slice(startIdx);
            activeRsis = rawData.rsis.slice(startIdx);
            const maxYield = rawData.stats[periodKey].max_yield;
            activeFloors = rawData.dps.slice(startIdx).map(d => maxYield > 0 ? Math.round(d / (maxYield / 100)) : 0);
            activeSnipers = activePrices.map((p, i) => (p <= activeFloors[i] * 1.03 && activeRsis[i] <= 45) ? p : null);
        }}
        sliceDataForPeriod('5Y');

        const ctxMain = document.getElementById('mainChart').getContext('2d');
        const mainChart = new Chart(ctxMain, {{
            type: 'line',
            data: {{
                labels: activeDates,
                datasets: [
                    {{ label: '🟢 저점신호', data: activeSnipers, borderColor: '#10b981', backgroundColor: '#10b981', pointRadius: 6, showLine: false, yAxisID: 'y_price', order: 1 }},
                    {{ label: '주가 (원)', data: activePrices, borderColor: '#ffffff', borderWidth: 2, tension: 0.1, pointRadius: 0, yAxisID: 'y_price', order: 2 }},
                    {{ label: '배당수익률 (%)', data: activeYields, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.08)', borderWidth: 2, fill: true, tension: 0.2, pointRadius: 0, yAxisID: 'y_yield', order: 3 }},
                    {{ label: '바닥선', data: activeFloors, borderColor: '#ef4444', borderWidth: 1.8, borderDash: [4, 4], pointRadius: 0, yAxisID: 'y_price', order: 4 }}
                ]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false, interaction: {{ mode: 'index', intersect: false }},
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false, external: function(context) {{ if (context.tooltip && context.tooltip.dataPoints && context.tooltip.dataPoints.length > 0) updateHud(context.tooltip.dataPoints[0].dataIndex); }} }} }},
                scales: {{
                    x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#94a3b8', maxTicksLimit: 8, font: {{size: 10}} }} }},
                    y_price: {{ type: 'linear', position: 'left', grid: {{ color: '#334155' }}, ticks: {{ color: '#ffffff', font: {{size: 10}}, callback: v => v.toLocaleString() + '원' }} }},
                    y_yield: {{ type: 'linear', position: 'right', reverse: true, grid: {{ drawOnChartArea: false }}, ticks: {{ color: '#3b82f6', font: {{size: 10}}, callback: v => v.toFixed(1) + '%' }} }}
                }}
            }}
        }});

        const ctxRsi = document.getElementById('rsiChart').getContext('2d');
        const rsiChart = new Chart(ctxRsi, {{
            type: 'line',
            data: {{ labels: activeDates, datasets: [{{ data: activeRsis, borderColor: '#22d3ee', borderWidth: 1.5, pointRadius: 0, tension: 0.1 }}] }},
            options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ display: false }}, y: {{ min: 0, max: 100, grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b', stepSize: 30, font: {{size: 9}} }} }} }} }}
        }});

        function updateHud(idx) {{
            if (idx < 0 || idx >= activeDates.length) return;
            document.getElementById('hudDate').innerText = activeDates[idx];
            document.getElementById('hudPrice').innerText = activePrices[idx].toLocaleString() + '원';
            document.getElementById('hudYield').innerText = activeYields[idx].toFixed(2) + '%';
            document.getElementById('hudFloor').innerText = activeFloors[idx].toLocaleString() + '원';
            document.getElementById('rsiHud').innerText = 'RSI: ' + activeRsis[idx].toFixed(1);
        }}
        updateHud(activeDates.length - 1);

        function changePeriod(key) {{
            currentPeriod = key;
            ['1Y', '3Y', '5Y', '10Y'].forEach(k => {{
                const btn = document.getElementById('btn' + k);
                if (btn) btn.className = (k === key) ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-blue-600 text-white transition" : "px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition";
            }});
            document.getElementById('hudFloorLabel').innerText = (key === '1Y' ? '1년' : (key === '3Y' ? '3년' : (key === '5Y' ? '5년' : '10년')));
            sliceDataForPeriod(key);
            mainChart.data.labels = activeDates;
            mainChart.data.datasets[0].data = activeSnipers;
            mainChart.data.datasets[1].data = activePrices;
            mainChart.data.datasets[2].data = activeYields;
            mainChart.data.datasets[3].data = activeFloors;
            mainChart.update();
            rsiChart.data.labels = activeDates;
            rsiChart.data.datasets[0].data = activeRsis;
            rsiChart.update();
            updateHud(activeDates.length - 1);
        }}

        function toggleLayers() {{
            mainChart.data.datasets[0].hidden = !document.getElementById('chkSniper').checked;
            mainChart.data.datasets[1].hidden = !document.getElementById('chkPrice').checked;
            mainChart.data.datasets[2].hidden = !document.getElementById('chkYield').checked;
            mainChart.data.datasets[3].hidden = !document.getElementById('chkFloor').checked;
            mainChart.options.scales.y_yield.display = document.getElementById('chkYield').checked;
            mainChart.update();
        }}

        // 실적 성장 차트 엔진
        const finRaw = {json.dumps(fin)};
        let curFinFreq = 'quarterly';
        let curFinPeriod = '5Y';
        const finPeriodCount = {{ 'quarterly': {{ '1Y': 4, '3Y': 12, '5Y': 20, '10Y': 40 }}, 'semiannual': {{ '1Y': 2, '3Y': 6, '5Y': 10, '10Y': 20 }}, 'annual': {{ '1Y': 1, '3Y': 3, '5Y': 5, '10Y': 10 }} }};
        let curGrowthLabels = [], curGrowthPrices = [], curGrowthProfit = [], curGrowthRev = [], curGrowthOpm = [], curGrowthYoY = [];

        function updateGrowthHud(idx) {{
            if (idx < 0 || idx >= curGrowthLabels.length) return;
            document.getElementById('gHudDate').innerText = curGrowthLabels[idx];
            document.getElementById('gHudPrice').innerText = (curGrowthPrices[idx] || 0).toLocaleString() + '원';
            document.getElementById('gHudProf').innerText = (curGrowthProfit[idx] || 0).toLocaleString() + '억원';
            document.getElementById('gHudYoY').innerText = ((curGrowthYoY[idx] || 0) >= 0 ? '+' : '') + (curGrowthYoY[idx] || 0).toFixed(1) + '%';
            document.getElementById('gHudOpm').innerText = (curGrowthOpm[idx] || 0).toFixed(1) + '%';
        }}

        const ctxGrowth = document.getElementById('growthChart').getContext('2d');
        const growthChart = new Chart(ctxGrowth, {{
            type: 'bar',
            data: {{
                labels: [],
                datasets: [
                    {{ type: 'line', label: '주가 (원)', data: [], borderColor: '#ffffff', borderWidth: 2, pointRadius: 3, yAxisID: 'y_price', order: 1 }},
                    {{ type: 'line', label: 'YoY 성장률 (%)', data: [], borderColor: '#38bdf8', borderWidth: 2, borderDash: [2, 2], pointRadius: 3, yAxisID: 'y_growth', order: 2 }},
                    {{ type: 'line', label: '영업이익률 OPM (%)', data: [], borderColor: '#fbbf24', backgroundColor: '#fbbf24', borderWidth: 2, pointRadius: 3, yAxisID: 'y_opm', order: 3 }},
                    {{ type: 'bar', label: '영업이익 (억원)', data: [], backgroundColor: '#10b981', borderRadius: 4, maxBarThickness: 16, categoryPercentage: 0.7, barPercentage: 0.8, yAxisID: 'y_profit', order: 4 }}
                ]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false, interaction: {{ mode: 'index', intersect: false }},
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false, external: function(context) {{ if (context.tooltip && context.tooltip.dataPoints && context.tooltip.dataPoints.length > 0) updateGrowthHud(context.tooltip.dataPoints[0].dataIndex); }} }} }},
                scales: {{
                    x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#cbd5e1', font: {{size: 10}} }} }},
                    y_profit: {{ type: 'linear', position: 'left', grid: {{ color: '#334155' }}, ticks: {{ color: '#10b981', font: {{size: 10}}, callback: v => v.toLocaleString() + '억' }} }},
                    y_price: {{ type: 'linear', position: 'right', grid: {{ drawOnChartArea: false }}, ticks: {{ color: '#ffffff', font: {{size: 10}}, callback: v => v.toLocaleString() + '원' }} }},
                    y_growth: {{ type: 'linear', position: 'right', grid: {{ drawOnChartArea: false }}, ticks: {{ color: '#38bdf8', font: {{size: 10}}, callback: v => v + '%' }} }},
                    y_opm: {{ type: 'linear', position: 'right', display: false }}
                }}
            }}
        }});

        function updateGrowthChart() {{
            const targetData = finRaw[curFinFreq];
            if (!targetData || !targetData.labels || targetData.labels.length === 0) return;
            const totalAvail = targetData.labels.length;
            const targetLimit = finPeriodCount[curFinFreq][curFinPeriod] || totalAvail;
            const actualCount = Math.min(totalAvail, targetLimit);
            const startIdx = Math.max(0, totalAvail - actualCount);

            curGrowthLabels = targetData.labels.slice(startIdx);
            curGrowthProfit = targetData.profit.slice(startIdx);
            curGrowthRev = targetData.revenue.slice(startIdx);
            curGrowthOpm = targetData.opm.slice(startIdx);
            curGrowthPrices = (targetData.prices || []).slice(startIdx);
            curGrowthYoY = (targetData.growth_yoy || []).slice(startIdx);
            const curNet = (targetData.net || []).slice(startIdx);

            growthChart.data.labels = curGrowthLabels;
            growthChart.data.datasets[0].data = curGrowthPrices;
            growthChart.data.datasets[1].data = curGrowthYoY;
            growthChart.data.datasets[2].data = curGrowthOpm;
            growthChart.data.datasets[3].data = curGrowthProfit;
            growthChart.update();

            if (curGrowthProfit.length >= 2) {{
                const firstP = curGrowthProfit[0], lastP = curGrowthProfit[curGrowthProfit.length - 1];
                const pChange = firstP !== 0 ? (((lastP - firstP) / Math.abs(firstP)) * 100) : 0;
                document.getElementById('cardProfGrowth').innerText = (pChange >= 0 ? '+' : '') + pChange.toFixed(1) + '%';
                document.getElementById('cardProfGrowth').className = pChange >= 0 ? 'text-lg font-bold text-red-400 mt-0.5' : 'text-lg font-bold text-blue-400 mt-0.5';
                document.getElementById('cardProfSub').innerText = curGrowthLabels[0] + ' 대비 ' + curGrowthLabels[curGrowthLabels.length - 1] + ' 변화';

                const firstOpm = curGrowthOpm[0], lastOpm = curGrowthOpm[curGrowthOpm.length - 1], opmDiff = lastOpm - firstOpm;
                const avgOpm = curGrowthOpm.reduce((a, b) => a + b, 0) / curGrowthOpm.length;
                document.getElementById('cardOpm').innerText = (opmDiff >= 0 ? '+' : '') + opmDiff.toFixed(1) + '%p';
                document.getElementById('cardOpm').className = opmDiff >= 0 ? 'text-lg font-bold text-emerald-400 mt-0.5' : 'text-lg font-bold text-blue-400 mt-0.5';
                document.getElementById('cardOpmSub').innerText = '시작 ' + firstOpm.toFixed(1) + '% ➔ 최근 ' + lastOpm.toFixed(1) + '% (평균 ' + avgOpm.toFixed(1) + '%)';

                const firstR = curGrowthRev[0], lastR = curGrowthRev[curGrowthRev.length - 1];
                const rChange = firstR !== 0 ? (((lastR - firstR) / Math.abs(firstR)) * 100) : 0;
                document.getElementById('cardRevGrowth').innerText = (rChange >= 0 ? '+' : '') + rChange.toFixed(1) + '%';
                document.getElementById('cardRevGrowth').className = rChange >= 0 ? 'text-lg font-bold text-red-400 mt-0.5' : 'text-lg font-bold text-blue-400 mt-0.5';
                document.getElementById('cardRevSub').innerText = firstR.toLocaleString() + '억 ➔ ' + lastR.toLocaleString() + '억';

                const totalNet = curNet.reduce((a, b) => a + b, 0);
                const hasDeficit = curNet.some(v => v < 0);
                document.getElementById('cardNet').innerText = totalNet.toLocaleString() + '억원';
                document.getElementById('cardNetSub').innerText = hasDeficit ? '⚠️ 일부 적자 발생' : '🛡️ 전 구간 흑자 지속';
            }}
            updateGrowthHud(curGrowthLabels.length - 1);
        }}

        function changeFinFreq(freq) {{
            curFinFreq = freq;
            document.getElementById('btnFreqQ').className = (freq === 'quarterly') ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-emerald-600 text-white transition" : "px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition";
            document.getElementById('btnFreqS').className = (freq === 'semiannual') ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-emerald-600 text-white transition" : "px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition";
            document.getElementById('btnFreqA').className = (freq === 'annual') ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-emerald-600 text-white transition" : "px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition";
            updateGrowthChart();
        }}

        function changeFinPeriod(period) {{
            curFinPeriod = period;
            ['1Y', '3Y', '5Y', '10Y'].forEach(p => {{
                const btn = document.getElementById('btnFin' + p);
                if (btn) btn.className = (p === period) ? "px-2.5 py-1 rounded-lg text-xs font-bold bg-blue-600 text-white transition" : "px-2.5 py-1 rounded-lg text-xs font-semibold text-slate-400 hover:text-white transition";
            }});
            updateGrowthChart();
        }}

        function toggleGrowthLayers() {{
            growthChart.data.datasets[0].hidden = !document.getElementById('chkGrowthPrice').checked;
            growthChart.data.datasets[1].hidden = !document.getElementById('chkGrowthYoY').checked;
            growthChart.data.datasets[2].hidden = !document.getElementById('chkGrowthOpm').checked;
            growthChart.data.datasets[3].hidden = !document.getElementById('chkGrowthProf').checked;
            growthChart.options.scales.y_price.display = document.getElementById('chkGrowthPrice').checked;
            growthChart.options.scales.y_growth.display = document.getElementById('chkGrowthYoY').checked;
            growthChart.update();
        }}
        updateGrowthChart();
    </script>
</body>
</html>
"""

    with open(file_name, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ [{data['name']}] v39.6 대시보드 렌더링 완료!")
    return html_content


# ---------- Streamlit UI ----------
st.set_page_config(layout="wide", page_title="주식 융합 대시보드")

st.title("📊 종목별 맞춤형 동적 가중치 대시보드")
st.markdown(
    "종목명, 일부 단어 또는 종목코드를 검색하세요. "
    "**검색어가 종목명에 포함된 모든 종목을 찾아 선택할 수 있습니다.**"
)

user_query = st.text_input(
    "🔎 종목 검색",
    value="",
    placeholder="예: 부동산 / 리츠 / 하이닉스 / 삼성 / 005930"
).strip()

if user_query:
    selected_code = None
    selected_name = None

    # 1) 6자리 종목코드
    if user_query.isdigit() and len(user_query) == 6:
        results = search_stock_list(user_query, max_results=10)
        if results:
            if len(results) == 1:
                selected_code = results[0]['code']
                selected_name = results[0]['name']
            else:
                options = [f"{x['name']} ({x['code']})" for x in results]
                selected_option = st.selectbox("분석할 종목을 선택하세요", options, key="stock_selector")
                idx = options.index(selected_option)
                selected_code = results[idx]['code']
                selected_name = results[idx]['name']
        else:
            # 코드가 KRX 목록에 없더라도 네이버에서 종목명을 확인
            selected_code = user_query
            try:
                url = f"https://finance.naver.com/item/main.naver?code={selected_code}"
                res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
                soup = BeautifulSoup(res.text, 'html.parser')
                name_elem = soup.select_one('.wrap_company h2 a')
                selected_name = name_elem.text.strip() if name_elem else None
            except Exception:
                selected_name = None

            if not selected_name:
                st.error("해당 종목코드를 찾지 못했습니다. 코드를 확인해주세요.")

    # 2) 종목명/부분 검색
    else:
        search_results = search_stock_list(user_query, max_results=200)

        if not search_results:
            st.warning(
                f"'{user_query}'가 종목명에 포함된 종목을 찾지 못했습니다. "
                "다른 검색어를 입력해보세요."
            )
        else:
            st.subheader(f"🔍 '{user_query}' 검색 결과 ({len(search_results)}개)")
            st.caption("종목명에 검색어가 포함된 종목을 모두 표시합니다. 아래에서 분석할 종목을 선택하세요.")

            options = [f"{item['name']}  |  {item['code']}" for item in search_results]
            selected_option = st.selectbox(
                "📌 분석할 종목 선택",
                options,
                key=f"stock_selector_{user_query}"
            )

            if selected_option:
                selected_idx = options.index(selected_option)
                selected_code = search_results[selected_idx]['code']
                selected_name = search_results[selected_idx]['name']

            # 검색 결과를 간단한 표로도 보여줘 선택 대상을 확인하기 쉽게 함
            result_table = pd.DataFrame(search_results)
            result_table.columns = ['종목코드', '종목명']
            st.dataframe(
                result_table,
                use_container_width=True,
                hide_index=True,
                height=min(420, 35 * len(result_table) + 45)
            )

    # 3) 선택한 종목 분석
    if selected_code and selected_name:
        st.info(f"선택 종목: **{selected_name} ({selected_code})**")
        if st.button("🚀 선택 종목 분석 실행", type="primary", use_container_width=True):
            with st.spinner(f"'{selected_name}' 데이터를 수집하고 대시보드를 생성하는 중입니다..."):
                try:
                    html_str = generate_v39_dashboard(
                        query=user_query,
                        code=selected_code,
                        name=selected_name
                    )
                    if html_str:
                        st.components.v1.html(html_str, height=1200, scrolling=True)
                    else:
                        st.error("대시보드 생성에 실패했습니다.")
                except Exception as e:
                    logging.exception("대시보드 생성 오류")
                    st.error(f"오류가 발생했습니다: {str(e)}")

