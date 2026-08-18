# ============================================================
# [v39.5] 종목별 맞춤형 동적 가중치(Dynamic Weighting) 모델
#         + KRX 전체 종목 부분일치 검색 강화 버전
#
# 핵심 변경사항
# ------------------------------------------------------------
# 1. 종목 검색을 네이버 자동완성 API에만 의존하지 않음
# 2. FinanceDataReader KRX 전체 종목 목록을 이용한 부분일치 검색
# 3. "부동산" 검색 시 종목명에 "부동산"이 들어가는 모든 종목 검색
# 4. "리츠", "삼성", "하이닉스" 등도 부분 문자열 검색
# 5. 검색 결과를 SelectBox로 선택
# 6. 검색 결과 전체를 표로 표시
# 7. 종목 선택 후 [분석 실행] 버튼을 눌러야 분석
# 8. KRX 목록은 6시간 캐시
# 9. 네이버 자동완성은 보조 검색원으로 사용
# 10. 종목 검색 실패 시 기존처럼 바로 "일치하는 종목이
#     없습니다"를 출력하지 않고 상세 안내
#
# 가격 데이터:
# FinanceDataReader → yfinance → Naver 월봉
#
# 배당 데이터:
# yfinance → Naver → 기본값
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
from datetime import datetime, timedelta

import yfinance as yf
import FinanceDataReader as fdr

import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# 기본 설정
# ============================================================

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

st.set_page_config(
    layout="wide",
    page_title="주식 융합 대시보드"
)


# ============================================================
# 전역 변수
# ============================================================

krx_df = pd.DataFrame()


# ============================================================
# KRX 전체 종목 목록 로딩
# ============================================================

@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def load_krx_listing():
    """
    KRX 전체 종목 목록을 가져온다.

    반환:
        DataFrame
        Code : 6자리 종목코드
        Name : 종목명

    FinanceDataReader 버전에 따라 컬럼이 달라질 수 있으므로
    방어적으로 처리한다.
    """

    try:
        df = fdr.StockListing("KRX")

        if df is None or df.empty:
            logging.warning("KRX 종목 목록이 비어 있습니다.")
            return pd.DataFrame(columns=["Code", "Name"])

        if "Code" not in df.columns or "Name" not in df.columns:
            logging.warning(
                f"KRX 데이터에 Code/Name 컬럼이 없습니다. "
                f"현재 컬럼: {list(df.columns)}"
            )
            return pd.DataFrame(columns=["Code", "Name"])

        out = df[["Code", "Name"]].copy()

        out.columns = ["Code", "Name"]

        # 종목코드 정리
        out["Code"] = (
            out["Code"]
            .astype(str)
            .str.extract(r"(\d{6})", expand=False)
        )

        # 종목명 HTML 태그 제거
        out["Name"] = (
            out["Name"]
            .astype(str)
            .str.replace(r"<[^>]+>", "", regex=True)
            .str.strip()
        )

        out = out.dropna(subset=["Code", "Name"])

        out = out[
            (out["Code"].str.len() == 6) &
            (out["Name"] != "") &
            (out["Name"].str.lower() != "nan")
        ]

        out = out.drop_duplicates(
            subset=["Code"],
            keep="first"
        )

        out = out.reset_index(drop=True)

        logging.info(
            f"KRX 종목 목록 로딩 완료: {len(out)}개"
        )

        return out

    except Exception as e:

        logging.exception(
            f"KRX 종목 목록 로딩 실패: {e}"
        )

        return pd.DataFrame(
            columns=["Code", "Name"]
        )


# ============================================================
# 네이버 자동완성 검색
# ============================================================

def search_naver_autocomplete(query):
    """
    네이버 금융 자동완성 API 검색.

    네이버 검색이 실패해도 전체 검색은 중단하지 않는다.
    """

    query = str(query or "").strip()

    if not query:
        return []

    url = "https://ac.finance.naver.com/ac"

    params = {
        "q": query,
        "target": "stock",
        "mode": "json"
    }

    headers = {
        "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
    }

    try:

        res = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=5
        )

        res.raise_for_status()

        data = res.json()

        result = []

        items = data.get("items", [])

        for item in items:

            if not isinstance(item, list):
                continue

            if len(item) < 2:
                continue

            code = str(item[0]).strip()

            name = re.sub(
                r"<[^>]+>",
                "",
                str(item[1])
            ).strip()

            if not re.fullmatch(
                r"\d{6}",
                code
            ):
                continue

            if not name:
                continue

            result.append(
                {
                    "code": code,
                    "name": name
                }
            )

        return result

    except Exception as e:

        logging.warning(
            f"네이버 자동완성 검색 실패: {e}"
        )

        return []


# ============================================================
# 종목 검색 통합 엔진
# ============================================================

def search_stock_list(query, max_results=200):
    """
    종목명 / 종목코드 통합 검색 엔진.

    검색 원칙
    --------------------------------------------------------
    1. 숫자 6자리:
       종목코드 검색

    2. 문자:
       KRX 전체 종목명에서 부분 문자열 검색

    3. 네이버 자동완성:
       KRX 검색을 보완

    예:
        부동산
        리츠
        삼성
        하이닉스
        반도체

    모두 종목명에 해당 문자열이 포함된 종목을 검색한다.
    """

    query = str(query or "").strip()

    if not query:
        return []

    # --------------------------------------------------------
    # 숫자 종목코드 검색
    # --------------------------------------------------------

    if query.isdigit():

        if len(query) > 6:
            return []

        qcode = query.zfill(6)

        df = load_krx_listing()

        if not df.empty:

            hit = df[
                df["Code"] == qcode
            ]

            if not hit.empty:

                return [
                    {
                        "code": str(row.Code),
                        "name": str(row.Name)
                    }
                    for row in hit.itertuples(index=False)
                ]

        # KRX 검색 실패 시 네이버 보완
        return search_naver_autocomplete(qcode)

    # --------------------------------------------------------
    # 문자 검색
    # --------------------------------------------------------

    q = query.casefold()

    results = []

    # --------------------------------------------------------
    # 1. 네이버 자동완성
    # --------------------------------------------------------

    naver_results = search_naver_autocomplete(query)

    results.extend(naver_results)

    # --------------------------------------------------------
    # 2. KRX 전체 종목 검색
    # --------------------------------------------------------

    df = load_krx_listing()

    if not df.empty:

        names = (
            df["Name"]
            .astype(str)
            .str.strip()
        )

        names_folded = names.str.casefold()

        # 핵심:
        # regex=False
        #
        # 따라서 검색어가 정규식으로 해석되지 않고
        # 단순 문자열로 정확하게 포함 여부를 판단한다.
        mask = names_folded.str.contains(
            q,
            regex=False,
            na=False
        )

        hits = df.loc[
            mask,
            ["Code", "Name"]
        ].copy()

        if not hits.empty:

            hit_names = (
                hits["Name"]
                .astype(str)
                .str.casefold()
            )

            # 정확히 같은 종목명
            exact = hits[
                hit_names == q
            ]

            # 검색어로 시작하는 종목
            starts = hits[
                hit_names.str.startswith(q) &
                (hit_names != q)
            ]

            # 검색어가 중간에 포함된 종목
            contains = hits[
                ~hit_names.str.startswith(q)
            ]

            ordered = pd.concat(
                [
                    exact,
                    starts,
                    contains
                ],
                ignore_index=True
            )

            for row in ordered.itertuples(
                index=False
            ):

                results.append(
                    {
                        "code": str(row.Code),
                        "name": str(row.Name)
                    }
                )

    # --------------------------------------------------------
    # 3. 중복 제거
    # --------------------------------------------------------

    unique = []

    seen = set()

    for item in results:

        code = str(
            item.get("code", "")
        ).strip()

        name = str(
            item.get("name", "")
        ).strip()

        if not code or not name:
            continue

        key = code

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            {
                "code": code,
                "name": name
            }
        )

        if len(unique) >= max_results:
            break

    return unique


# ============================================================
# 종목명 / 코드 정밀 매칭
# ============================================================

def get_code_and_name(query):

    query = str(query or "").strip()

    if not query:
        return None, None

    # --------------------------------------------------------
    # 숫자 코드
    # --------------------------------------------------------

    if query.isdigit() and len(query) <= 6:

        code = query.zfill(6)

        results = search_stock_list(
            code,
            max_results=1
        )

        if results:

            return (
                results[0]["code"],
                results[0]["name"]
            )

        # 네이버 직접 확인
        try:

            url = (
                "https://finance.naver.com/"
                f"item/main.naver?code={code}"
            )

            res = requests.get(
                url,
                headers={
                    "User-Agent":
                        "Mozilla/5.0"
                },
                timeout=5
            )

            soup = BeautifulSoup(
                res.text,
                "html.parser"
            )

            name_elem = soup.select_one(
                ".wrap_company h2 a"
            )

            if name_elem:

                return (
                    code,
                    name_elem.text.strip()
                )

        except Exception:
            pass

        return code, code

    # --------------------------------------------------------
    # 문자 검색
    # --------------------------------------------------------

    results = search_stock_list(
        query,
        max_results=1
    )

    if results:

        return (
            results[0]["code"],
            results[0]["name"]
        )

    return None, None


# ============================================================
# 네이버 월봉 가격 데이터
# ============================================================

def _get_naver_monthly_price(code):
    """
    네이버 금융 월봉 데이터를 스크래핑한다.
    """

    url = (
        "https://finance.naver.com/"
        f"item/sise_month.nhn?code={code}"
    )

    headers = {
        "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
    }

    try:

        res = requests.get(
            url,
            headers=headers,
            timeout=10
        )

        res.encoding = "cp949"

        tables = pd.read_html(
            StringIO(res.text)
        )

        if not tables:
            return pd.DataFrame()

        df = tables[0].copy()

        if "날짜" not in df.columns:
            return pd.DataFrame()

        df["날짜"] = pd.to_datetime(
            df["날짜"],
            format="%Y.%m.%d",
            errors="coerce"
        )

        df = df.dropna(
            subset=["날짜"]
        )

        df = df.set_index("날짜")

        df = df.rename(
            columns={
                "시가": "Open",
                "고가": "High",
                "저가": "Low",
                "종가": "Close",
                "거래량": "Volume"
            }
        )

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col in required:

            if col not in df.columns:
                df[col] = np.nan

        df = df[required]

        df = df.apply(
            pd.to_numeric,
            errors="coerce"
        )

        df = df.dropna(
            subset=["Close"]
        )

        df = df.sort_index()

        return df

    except Exception as e:

        logging.warning(
            f"네이버 월봉 데이터 실패: {e}"
        )

        return pd.DataFrame()


# ============================================================
# 가격 데이터 다중 소스 폴백
# ============================================================

def get_price_data(
    code,
    name,
    start_date
):
    """
    가격 데이터 수집 순서:

    1. FinanceDataReader
    2. yfinance
    3. Naver 월봉
    """

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    # --------------------------------------------------------
    # 1. FinanceDataReader
    # --------------------------------------------------------

    try:

        df = fdr.DataReader(
            code,
            start=start_date
        )

        if (
            df is not None and
            not df.empty
        ):

            for col in required:

                if col not in df.columns:
                    df[col] = np.nan

            df = df[required]

            df = df.apply(
                pd.to_numeric,
                errors="coerce"
            )

            df = df.dropna(
                subset=["Close"]
            )

            return df

    except Exception as e:

        logging.warning(
            f"FinanceDataReader 실패: {e}"
        )

    # --------------------------------------------------------
    # 2. yfinance
    # --------------------------------------------------------

    for suffix in [".KS", ".KQ"]:

        ticker = f"{code}{suffix}"

        try:

            yf_ticker = yf.Ticker(
                ticker
            )

            df = yf_ticker.history(
                start=start_date,
                auto_adjust=False
            )

            if (
                df is not None and
                not df.empty
            ):

                for col in required:

                    if col not in df.columns:
                        df[col] = np.nan

                df = df[required]

                if hasattr(
                    df.index,
                    "tz"
                ):

                    if df.index.tz is not None:
                        df.index = (
                            df.index
                            .tz_localize(None)
                        )

                df = df.apply(
                    pd.to_numeric,
                    errors="coerce"
                )

                df = df.dropna(
                    subset=["Close"]
                )

                if not df.empty:
                    return df

        except Exception as e:

            logging.warning(
                f"yfinance 실패 "
                f"{ticker}: {e}"
            )

    # --------------------------------------------------------
    # 3. 네이버 월봉
    # --------------------------------------------------------

    df_naver = _get_naver_monthly_price(
        code
    )

    if not df_naver.empty:

        cutoff = (
            datetime.today()
            - timedelta(days=365 * 11)
        )

        df_naver = df_naver[
            df_naver.index >= cutoff
        ]

        return df_naver

    return pd.DataFrame()


# ============================================================
# DPS 자동 수집
# ============================================================

def get_dps_automatically(
    code,
    name
):
    """
    최근 1년 DPS 자동 계산.

    순서:
        yfinance
        →
        Naver
        →
        종목 유형별 기본값
    """

    # --------------------------------------------------------
    # 1. yfinance 배당
    # --------------------------------------------------------

    for suffix in [
        ".KS",
        ".KQ"
    ]:

        try:

            ticker = yf.Ticker(
                f"{code}{suffix}"
            )

            divs = ticker.dividends

            if (
                divs is not None and
                not divs.empty
            ):

                one_year_ago = (
                    datetime.today()
                    - timedelta(days=365)
                )

                # timezone 문제 방어
                try:

                    if divs.index.tz is not None:

                        divs.index = (
                            divs.index
                            .tz_localize(None)
                        )

                except Exception:
                    pass

                recent_divs = divs[
                    divs.index >= one_year_ago
                ]

                if not recent_divs.empty:

                    dps_val = float(
                        recent_divs.sum()
                    )

                    if dps_val > 0:

                        return dps_val

        except Exception:

            continue

    # --------------------------------------------------------
    # 2. 네이버
    # --------------------------------------------------------

    try:

        url = (
            "https://finance.naver.com/"
            f"item/main.naver?code={code}"
        )

        res = requests.get(
            url,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },
            timeout=5
        )

        res.encoding = "cp949"

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        # 우선 "주당배당금" 문구 탐색
        text = soup.get_text(
            " ",
            strip=True
        )

        patterns = [
            r"주당배당금\s*([0-9,]+)",
            r"배당금\s*([0-9,]+)"
        ]

        for pattern in patterns:

            matches = re.findall(
                pattern,
                text
            )

            if matches:

                for value in matches:

                    try:

                        val = float(
                            value.replace(",", "")
                        )

                        if val > 0:
                            return val

                    except Exception:
                        continue

        # 기존 DOM 방식 보완
        for elem in soup.find_all(
            ["em", "th", "td"]
        ):

            if (
                "주당배당금" in elem.text or
                "배당금" in elem.text
            ):

                nxt = elem.find_next_sibling()

                if nxt:

                    nums = re.findall(
                        r"[\d,]+",
                        nxt.text
                    )

                    if nums:

                        val = float(
                            nums[0]
                            .replace(",", "")
                        )

                        if val > 0:
                            return val

    except Exception:

        pass

    # --------------------------------------------------------
    # 3. 최소 기본값
    # --------------------------------------------------------

    if (
        "리츠" in name or
        "부동산" in name or
        "맥쿼리" in name
    ):
        return 730.0

    return 350.0


# ============================================================
# 뉴스 및 공시
# ============================================================

def get_news_and_disclosures(code):

    headers = {
        "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36",
        "Referer":
            f"https://finance.naver.com/item/main.naver?code={code}"
    }

    news_list = []
    notice_list = []

    # --------------------------------------------------------
    # 뉴스
    # --------------------------------------------------------

    try:

        url_news = (
            "https://finance.naver.com/"
            f"item/news_news.naver?code={code}&page=1"
        )

        res = requests.get(
            url_news,
            headers=headers,
            timeout=10
        )

        res.encoding = "cp949"

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        table = soup.select_one(
            "table.type5"
        )

        if table:

            for row in table.select("tr"):

                title_elem = row.select_one(
                    "a.tit"
                )

                if not title_elem:
                    continue

                title = title_elem.get_text(
                    " ",
                    strip=True
                )

                link = title_elem.get(
                    "href",
                    ""
                )

                if link.startswith("/"):
                    link = (
                        "https://finance.naver.com"
                        + link
                    )

                info = row.select(
                    "td"
                )

                press = ""

                date = ""

                if len(info) >= 2:

                    press = info[-2].get_text(
                        " ",
                        strip=True
                    )

                    date = info[-1].get_text(
                        " ",
                        strip=True
                    )

                news_list.append(
                    {
                        "tag": "NEWS",
                        "title": title,
                        "link": link,
                        "press": press,
                        "date": date
                    }
                )

                if len(news_list) >= 15:
                    break

    except Exception as e:

        logging.warning(
            f"뉴스 수집 실패: {e}"
        )

    # --------------------------------------------------------
    # 공시
    # --------------------------------------------------------

    try:

        url_notice = (
            "https://finance.naver.com/"
            f"item/news_notice.naver?code={code}&page=1"
        )

        res = requests.get(
            url_notice,
            headers=headers,
            timeout=10
        )

        res.encoding = "cp949"

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        table = soup.select_one(
            "table.type5"
        )

        if table:

            for row in table.select("tr"):

                title_elem = row.select_one(
                    "a"
                )

                if not title_elem:
                    continue

                title = title_elem.get_text(
                    " ",
                    strip=True
                )

                link = title_elem.get(
                    "href",
                    ""
                )

                if link.startswith("/"):
                    link = (
                        "https://finance.naver.com"
                        + link
                    )

                cells = row.select(
                    "td"
                )

                press = ""
                date = ""

                if len(cells) >= 2:

                    press = cells[-2].get_text(
                        " ",
                        strip=True
                    )

                    date = cells[-1].get_text(
                        " ",
                        strip=True
                    )

                notice_list.append(
                    {
                        "tag": "공시",
                        "title": title,
                        "link": link,
                        "press": press,
                        "date": date
                    }
                )

                if len(notice_list) >= 15:
                    break

    except Exception as e:

        logging.warning(
            f"공시 수집 실패: {e}"
        )

    return news_list, notice_list


# ============================================================
# 재무 / 성장 모델
# ============================================================

def get_financial_data(
    code,
    name
):
    """
    재무 데이터.

    Yahoo Finance를 우선 사용하고
    실패하면 기본적인 안정값을 사용한다.
    """

    result = {
        "growth_model": {
            "growth_rate": 7.0,
            "target_peg_10": 0.0,
            "target_peg_05": 0.0
        },
        "financials": {}
    }

    # --------------------------------------------------------
    # yfinance
    # --------------------------------------------------------

    for suffix in [
        ".KS",
        ".KQ"
    ]:

        try:

            ticker = yf.Ticker(
                f"{code}{suffix}"
            )

            info = ticker.info

            if not info:
                continue

            growth_rate = (
                info.get(
                    "earningsGrowth"
                )
            )

            if growth_rate is None:

                growth_rate = (
                    info.get(
                        "revenueGrowth"
                    )
                )

            if growth_rate is not None:

                growth_rate = (
                    float(growth_rate)
                    * 100
                )

            else:

                growth_rate = 7.0

            if (
                not np.isfinite(
                    growth_rate
                )
            ):

                growth_rate = 7.0

            growth_rate = max(
                -20.0,
                min(
                    30.0,
                    growth_rate
                )
            )

            current_price = (
                info.get(
                    "currentPrice"
                )
                or info.get(
                    "regularMarketPrice"
                )
                or 0
            )

            trailing_eps = (
                info.get(
                    "trailingEps"
                )
                or 0
            )

            if (
                current_price and
                trailing_eps and
                trailing_eps > 0
            ):

                peg_fair = (
                    trailing_eps
                    * growth_rate
                    * 1.0
                )

                peg_bottom = (
                    trailing_eps
                    * growth_rate
                    * 0.7
                )

            else:

                peg_fair = (
                    float(current_price)
                    if current_price
                    else 0
                )

                peg_bottom = (
                    peg_fair * 0.7
                )

            result["growth_model"] = {
                "growth_rate":
                    growth_rate,
                "target_peg_10":
                    peg_fair,
                "target_peg_05":
                    peg_bottom
            }

            result["financials"] = {
                "market_cap":
                    info.get(
                        "marketCap",
                        0
                    ),
                "trailing_eps":
                    trailing_eps,
                "current_price":
                    current_price,
                "pe_ratio":
                    info.get(
                        "trailingPE",
                        0
                    )
            }

            return result

        except Exception as e:

            logging.warning(
                f"재무정보 수집 실패 "
                f"{code}{suffix}: {e}"
            )

    return result


# ============================================================
# 동적 가중치
# ============================================================

def calculate_dynamic_weights(
    current_yield,
    growth_rate
):
    """
    배당수익률과 성장률에 따라
    배당 / 성장 가중치를 동적으로 결정한다.
    """

    try:
        y = float(current_yield)
    except Exception:
        y = 0.0

    try:
        g = float(growth_rate)
    except Exception:
        g = 0.0

    # 기본
    w_div = 0.60
    w_growth = 0.40

    # 고배당
    if y >= 7.0:

        w_div = 0.75
        w_growth = 0.25

        profile = (
            "고배당형"
        )

    elif y >= 5.0:

        w_div = 0.70
        w_growth = 0.30

        profile = (
            "배당중심형"
        )

    elif y >= 3.0:

        w_div = 0.60
        w_growth = 0.40

        profile = (
            "균형형"
        )

    elif g >= 12.0:

        w_div = 0.40
        w_growth = 0.60

        profile = (
            "성장중심형"
        )

    else:

        w_div = 0.50
        w_growth = 0.50

        profile = (
            "중립형"
        )

    return (
        w_div,
        w_growth,
        profile
    )


# ============================================================
# 종합 데이터 계산
# ============================================================

def calculate_multi_period_engine(
    code,
    name
):

    now = datetime.now()

    start_date = (
        now
        - timedelta(days=365 * 11)
    )

    # --------------------------------------------------------
    # 가격 데이터
    # --------------------------------------------------------

    df = get_price_data(
        code,
        name,
        start_date
    )

    if df is None or df.empty:

        raise ValueError(
            "가격 데이터를 가져오지 못했습니다. "
            "네트워크 상태를 확인하거나 "
            "종목 코드를 확인해주세요."
        )

    if len(df) < 5:

        raise ValueError(
            "충분한 가격 데이터를 "
            "확보하지 못했습니다."
        )

    df = df.copy()

    df = df.sort_index()

    df["Close"] = pd.to_numeric(
        df["Close"],
        errors="coerce"
    )

    df = df.dropna(
        subset=["Close"]
    )

    if len(df) < 5:

        raise ValueError(
            "유효한 종가 데이터가 "
            "충분하지 않습니다."
        )

    # --------------------------------------------------------
    # 최신 가격
    # --------------------------------------------------------

    latest_price = int(
        round(
            float(
                df["Close"].iloc[-1]
            )
        )
    )

    prev_price = int(
        round(
            float(
                df["Close"].iloc[-2]
            )
        )
    )

    if prev_price > 0:

        change_pct = (
            (
                latest_price
                - prev_price
            )
            / prev_price
            * 100
        )

    else:

        change_pct = 0.0

    # --------------------------------------------------------
    # DPS
    # --------------------------------------------------------

    real_dps = get_dps_automatically(
        code,
        name
    )

    if real_dps <= 0:
        real_dps = 350.0

    # --------------------------------------------------------
    # 2주 단위 데이터
    # --------------------------------------------------------

    df_all = (
        df
        .resample("2W")
        .last()
        .dropna(
            subset=["Close"]
        )
    )

    if df_all.empty:

        df_all = df.copy()

    rolling_dps = []

    rolling_yields = []

    for dt, row in df_all.iterrows():

        p = float(
            row["Close"]
        )

        rolling_dps.append(
            real_dps
        )

        if p > 0:

            rolling_yields.append(
                real_dps
                / p
                * 100
            )

        else:

            rolling_yields.append(
                0.0
            )

    df_all["DPS_TTM"] = (
        rolling_dps
    )

    df_all["Yield"] = (
        rolling_yields
    )

    current_yield = float(
        df_all["Yield"].iloc[-1]
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    close_all = df_all["Close"]

    delta = close_all.diff()

    gain = (
        delta
        .where(
            delta > 0,
            0
        )
        .rolling(14)
        .mean()
    )

    loss = (
        -delta
        .where(
            delta < 0,
            0
        )
        .rolling(14)
        .mean()
    )

    rs = gain / loss

    df_all["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df_all["RSI"] = (
        df_all["RSI"]
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .fillna(50)
    )

    # --------------------------------------------------------
    # 기간별 배당수익률
    # --------------------------------------------------------

    periods_def = {
        "1Y": (
            "1년 (단기 바닥)",
            1,
            "1차 매수"
        ),
        "3Y": (
            "3년 (중기 바닥)",
            3,
            "2차 매수"
        ),
        "5Y": (
            "5년 (장기 안전마진)",
            5,
            "3차 매수"
        ),
        "10Y": (
            "10년 (역사적 대바닥)",
            10,
            "풀매수"
        )
    }

    matrix_table = []

    period_stats = {}

    for key, (
        label,
        years,
        allocation
    ) in periods_def.items():

        sub_df = df_all[
            df_all.index
            >= now
            - timedelta(
                days=365 * years
            )
        ]

        if sub_df.empty:

            sub_df = df_all

        if not sub_df.empty:

            p_max_yield = float(
                np.max(
                    sub_df["Yield"]
                )
            )

            p_min_yield = float(
                np.min(
                    sub_df["Yield"]
                )
            )

            p_avg_yield = float(
                np.mean(
                    sub_df["Yield"]
                )
            )

        else:

            p_max_yield = 3.67
            p_min_yield = 0.0
            p_avg_yield = 0.0

        if p_max_yield > 0:

            floor_price = int(
                real_dps
                / (
                    p_max_yield
                    / 100
                )
            )

        else:

            floor_price = 0

        if floor_price > 0:

            gap = (
                (
                    latest_price
                    - floor_price
                )
                / floor_price
                * 100
            )

        else:

            gap = 0.0

        diff_won = (
            latest_price
            - floor_price
        )

        status = (
            "🎯 매수 가능"
            if latest_price <= floor_price
            else "⏳ 대기 (비쌈)"
        )

        matrix_table.append(
            {
                "key": key,
                "period": label,
                "allocation": allocation,
                "max_yield": p_max_yield,
                "floor_price": floor_price,
                "gap": gap,
                "diff_won": diff_won,
                "status": status
            }
        )

        period_stats[key] = {
            "min_yield":
                p_min_yield,
            "avg_yield":
                p_avg_yield,
            "max_yield":
                p_max_yield
        }

    # --------------------------------------------------------
    # 성장 모델
    # --------------------------------------------------------

    fin_data = get_financial_data(
        code,
        name
    )

    gm = fin_data[
        "growth_model"
    ]

    growth_rate = float(
        gm.get(
            "growth_rate",
            7.0
        )
    )

    peg_fair = float(
        gm.get(
            "target_peg_10",
            latest_price
        )
    )

    peg_bottom = float(
        gm.get(
            "target_peg_05",
            latest_price * 0.7
        )
    )

    # PEG 가격이 0 또는 비정상일 경우
    if (
        not np.isfinite(
            peg_fair
        )
        or peg_fair <= 0
    ):

        peg_fair = float(
            latest_price
        )

    if (
        not np.isfinite(
            peg_bottom
        )
        or peg_bottom <= 0
    ):

        peg_bottom = (
            latest_price * 0.7
        )

    # --------------------------------------------------------
    # 동적 가중치
    # --------------------------------------------------------

    (
        w_div,
        w_growth,
        profile_desc
    ) = calculate_dynamic_weights(
        current_yield,
        growth_rate
    )

    # --------------------------------------------------------
    # 매수 가격
    # --------------------------------------------------------

    div_1y = (
        matrix_table[0]["floor_price"]
    )

    div_3y = (
        matrix_table[1]["floor_price"]
    )

    div_5y = (
        matrix_table[2]["floor_price"]
    )

    if div_1y <= 0:
        div_1y = latest_price

    if div_3y <= 0:
        div_3y = latest_price

    if div_5y <= 0:
        div_5y = latest_price

    buy_step_1 = int(
        div_1y * w_div
        + peg_fair * w_growth
    )

    buy_step_2 = int(
        div_3y * w_div
        + (
            peg_fair * 0.6
            + peg_bottom * 0.4
        )
        * w_growth
    )

    buy_step_3 = int(
        div_5y * w_div
        + peg_bottom * w_growth
    )

    # --------------------------------------------------------
    # 차트 데이터
    # --------------------------------------------------------

    chart_payload = {
        "dates":
            df_all.index
            .strftime("%y.%m.%d")
            .tolist(),

        "prices":
            df_all["Close"]
            .fillna(0)
            .astype(int)
            .tolist(),

        "yields":
            [
                round(
                    float(v),
                    2
                )
                for v
                in df_all["Yield"]
            ],

        "rsis":
            [
                round(
                    float(v),
                    1
                )
                for v
                in df_all["RSI"]
            ],

        "dps":
            [
                round(
                    float(v),
                    2
                )
                for v
                in df_all["DPS_TTM"]
            ],

        "stats":
            period_stats
    }

    return {
        "code": code,
        "name": name,

        "latest_price":
            latest_price,

        "change_pct":
            change_pct,

        "current_yield":
            current_yield,

        "current_dps":
            real_dps,

        "matrix":
            matrix_table,

        "buy_step_1":
            buy_step_1,

        "buy_step_2":
            buy_step_2,

        "buy_step_3":
            buy_step_3,

        "div_1y":
            div_1y,

        "div_5y":
            div_5y,

        "peg_fair":
            peg_fair,

        "peg_bottom":
            peg_bottom,

        "w_div":
            int(w_div * 100),

        "w_growth":
            int(w_growth * 100),

        "profile_desc":
            profile_desc,

        "fin_data":
            fin_data,

        "chart_payload":
            chart_payload
    }


# ============================================================
# HTML 대시보드 생성
# ============================================================

def generate_v39_dashboard(
    query,
    code=None,
    name=None
):

    if code is None or name is None:

        code, name = get_code_and_name(
            query
        )

    if not code or not name:

        return None

    data = calculate_multi_period_engine(
        code,
        name
    )

    if not data:

        return None

    news_items, notice_items = (
        get_news_and_disclosures(
            code
        )
    )

    fin = data["fin_data"]

    gm = fin.get(
        "growth_model",
        {}
    )

    file_name = (
        f"dividend_dashboard_{code}.html"
    )

    b1 = data["buy_step_1"]
    b2 = data["buy_step_2"]
    b3 = data["buy_step_3"]

    div_1y = data["div_1y"]
    div_5y = data["div_5y"]

    peg_fair = data["peg_fair"]
    peg_bottom = data["peg_bottom"]

    w_div = data["w_div"]
    w_growth = data["w_growth"]

    profile_desc = data[
        "profile_desc"
    ]

    matrix = data["matrix"]

    chart_payload_json = json.dumps(
        data["chart_payload"],
        ensure_ascii=False
    )

    # --------------------------------------------------------
    # 기간별 카드
    # --------------------------------------------------------

    matrix_cards = ""

    for item in matrix:

        status_class = (
            "text-emerald-400"
            if "매수 가능"
            in item["status"]
            else "text-amber-400"
        )

        matrix_cards += f"""
        <div class="bg-slate-950/70
                    p-4 rounded-xl
                    border border-slate-800">

            <div class="flex items-center
                        justify-between mb-2">

                <span class="text-sm
                             font-black
                             text-white">
                    {item['period']}
                </span>

                <span class="text-xs
                             font-bold
                             {status_class}">
                    {item['status']}
                </span>

            </div>

            <div class="grid grid-cols-2 gap-2">

                <div>
                    <p class="text-[10px]
                              text-slate-500">
                        최대 배당률
                    </p>

                    <p class="text-sm
                              font-bold
                              text-blue-300">
                        {item['max_yield']:.2f}%
                    </p>
                </div>

                <div>
                    <p class="text-[10px]
                              text-slate-500">
                        바닥 가격
                    </p>

                    <p class="text-sm
                              font-bold
                              text-white">
                        {item['floor_price']:,}원
                    </p>
                </div>

                <div>
                    <p class="text-[10px]
                              text-slate-500">
                        현재가 차이
                    </p>

                    <p class="text-sm
                              font-bold
                              text-slate-200">
                        {item['diff_won']:+,}원
                    </p>
                </div>

                <div>
                    <p class="text-[10px]
                              text-slate-500">
                        괴리율
                    </p>

                    <p class="text-sm
                              font-bold
                              text-slate-200">
                        {item['gap']:+.2f}%
                    </p>
                </div>

            </div>
        </div>
        """

    # --------------------------------------------------------
    # 뉴스 HTML
    # --------------------------------------------------------

    news_html = ""

    if news_items:

        for n in news_items:

            news_html += f"""
            <a href="{n['link']}"
               target="_blank"
               class="block p-3
                      bg-slate-950/60
                      hover:bg-slate-950
                      rounded-xl
                      border border-slate-800/80
                      transition">

                <div class="flex
                            items-center
                            justify-between
                            gap-1
                            mb-1.5">

                    <span class="px-2 py-0.5
                                 text-[9px]
                                 font-bold
                                 rounded
                                 bg-blue-950
                                 text-blue-300
                                 border
                                 border-blue-800">
                        {n['tag']}
                    </span>

                    <span class="text-[10px]
                                 text-slate-400">
                        {n['press']} · {n['date']}
                    </span>

                </div>

                <p class="text-xs
                          text-slate-200
                          font-medium
                          leading-snug">
                    {n['title']}
                </p>

            </a>
            """

    else:

        news_html = """
        <p class="text-xs
                  text-slate-400
                  text-center
                  py-16">
            뉴스가 없습니다.
        </p>
        """

    # --------------------------------------------------------
    # 공시 HTML
    # --------------------------------------------------------

    notice_html = ""

    if notice_items:

        for n in notice_items:

            notice_html += f"""
            <a href="{n['link']}"
               target="_blank"
               class="block p-3
                      bg-slate-950/60
                      hover:bg-slate-950
                      rounded-xl
                      border border-slate-800/80
                      transition">

                <div class="flex
                            items-center
                            justify-between
                            gap-1
                            mb-1.5">

                    <span class="px-2 py-0.5
                                 text-[9px]
                                 font-bold
                                 rounded
                                 bg-amber-950
                                 text-amber-300
                                 border
                                 border-amber-800">
                        {n['tag']}
                    </span>

                    <span class="text-[10px]
                                 text-slate-400">
                        {n['press']} · {n['date']}
                    </span>

                </div>

                <p class="text-xs
                          text-slate-200
                          font-medium
                          leading-snug">
                    {n['title']}
                </p>

            </a>
            """

    else:

        notice_html = """
        <p class="text-xs
                  text-slate-400
                  text-center
                  py-16">
            공시가 없습니다.
        </p>
        """

    # --------------------------------------------------------
    # HTML
    # --------------------------------------------------------

    html_content = f"""
<!DOCTYPE html>

<html lang="ko">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width,
               initial-scale=1.0">

<title>
[{code}] {data['name']}
종목 맞춤형 동적 가중치 대시보드
</title>

<script src="https://cdn.tailwindcss.com"></script>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>

@import url(
    'https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700;800&display=swap'
);

body {{
    font-family:
        'Pretendard',
        sans-serif;

    background-color:
        #0b0f19;

    color:
        #f1f5f9;
}}

.custom-scroll::-webkit-scrollbar {{
    width: 5px;
}}

.custom-scroll::-webkit-scrollbar-track {{
    background:
        #111827;
}}

.custom-scroll::-webkit-scrollbar-thumb {{
    background:
        #374151;

    border-radius:
        3px;
}}

</style>

</head>

<body class="p-3 md:p-6 custom-scroll">

<div class="max-w-7xl mx-auto space-y-4">


<!-- ====================================================== -->
<!-- 헤더 -->
<!-- ====================================================== -->

<div class="bg-slate-900/90
            p-5
            rounded-2xl
            border border-slate-800
            shadow-2xl
            backdrop-blur-md
            flex
            flex-col
            md:flex-row
            md:items-center
            justify-between
            gap-4">

    <div>

        <div class="flex
                    items-center
                    gap-3
                    flex-wrap">

            <h1 class="text-2xl
                       font-extrabold
                       text-white
                       tracking-tight">

                {data['name']}

            </h1>

            <span class="text-xs
                         px-2.5
                         py-1
                         bg-slate-800
                         text-blue-400
                         font-mono
                         rounded-lg
                         border
                         border-slate-700">

                {code}

            </span>

            <span class="text-xs
                         px-3
                         py-1
                         bg-indigo-950
                         text-indigo-300
                         font-bold
                         rounded-lg
                         border
                         border-indigo-800">

                {profile_desc}

                (배당 {w_div}%
                :
                성장 {w_growth}%)

            </span>

        </div>

        <p class="text-xs
                  text-slate-400
                  mt-2">

            현재 주가:

            <b class="text-white
                      text-base
                      font-extrabold">

                {data['latest_price']:,}원

            </b>

            ({data['change_pct']:+.2f}%)

            ·

            현재 배당수익률:

            <b class="text-blue-400
                      text-base
                      font-extrabold">

                {data['current_yield']:.2f}%

            </b>

            ·

            연간 DPS:

            <b class="text-emerald-400
                      text-base
                      font-extrabold">

                {data['current_dps']:,.0f}원

            </b>

        </p>

    </div>


    <div>

        <a href="https://finance.naver.com/item/main.naver?code={code}"
           target="_blank"
           class="text-xs
                  bg-slate-800
                  hover:bg-slate-700
                  text-slate-200
                  px-3.5
                  py-2
                  rounded-xl
                  border
                  border-slate-700
                  transition
                  inline-block">

            네이버 증권 열기 ↗

        </a>

    </div>

</div>


<!-- ====================================================== -->
<!-- 마스터 매매 결론 -->
<!-- ====================================================== -->

<div class="p-5
            rounded-2xl
            border
            shadow-2xl
            bg-gradient-to-r
            from-slate-900
            via-indigo-950/80
            to-slate-900
            border-indigo-500/50
            space-y-3">

    <div class="flex
                items-center
                justify-between
                border-b
                border-slate-800
                pb-2
                gap-2
                flex-wrap">

        <div class="flex
                    items-center
                    gap-2">

            <span class="px-2.5
                         py-0.5
                         text-xs
                         font-black
                         rounded-lg
                         bg-indigo-600
                         text-white">

                마스터 매매 결론

            </span>

            <h2 class="text-base
                       md:text-lg
                       font-black
                       text-indigo-200
                       tracking-tight">

                종목 맞춤형 동적 가중치
                3단계 매수가이드

            </h2>

        </div>

        <span class="text-xs
                     text-slate-400">

            실데이터 기반
            ·
            배당 {w_div}%
            +
            실적 성장 {w_growth}%

        </span>

    </div>


    <div class="grid
                grid-cols-1
                md:grid-cols-3
                gap-3
                pt-1">


        <div class="bg-slate-950/70
                    p-3
                    rounded-xl
                    border
                    border-slate-800">

            <p class="text-xs
                      text-blue-400
                      font-bold">

                1차 매수
                (30% 비중)

            </p>

            <h3 class="text-lg
                       font-black
                       text-white
                       mt-0.5">

                {b1:,}원

            </h3>

            <p class="text-[10px]
                      text-slate-400
                      mt-0.5">

                배당 1년 바닥
                +
                성장 적정가

            </p>

        </div>


        <div class="bg-slate-950/70
                    p-3
                    rounded-xl
                    border
                    border-slate-800">

            <p class="text-xs
                      text-emerald-400
                      font-bold">

                2차 매수
                (30% 비중)

            </p>

            <h3 class="text-lg
                       font-black
                       text-white
                       mt-0.5">

                {b2:,}원

            </h3>

            <p class="text-[10px]
                      text-slate-400
                      mt-0.5">

                배당 3년 바닥
                +
                성장 적정가

            </p>

        </div>


        <div class="bg-slate-950/70
                    p-3
                    rounded-xl
                    border
                    border-slate-800">

            <p class="text-xs
                      text-amber-400
                      font-bold">

                3차 매수
                (40% 비중)

            </p>

            <h3 class="text-lg
                       font-black
                       text-white
                       mt-0.5">

                {b3:,}원

            </h3>

            <p class="text-[10px]
                      text-slate-400
                      mt-0.5">

                배당 5년 바닥
                +
                성장 하단가

            </p>

        </div>

    </div>

</div>


<!-- ====================================================== -->
<!-- 배당 역사 -->
<!-- ====================================================== -->

<div class="bg-slate-900/80
            p-5
            rounded-2xl
            border border-slate-800">

    <div class="flex
                items-center
                justify-between
                mb-4
                flex-wrap
                gap-2">

        <div>

            <h2 class="text-lg
                       font-black
                       text-white">

                📊 배당수익률 역사

            </h2>

            <p class="text-xs
                      text-slate-500
                      mt-1">

                과거 주가와 현재 DPS를 기준으로
                계산한 역사적 배당수익률

            </p>

        </div>

        <div class="flex
                    gap-2
                    flex-wrap">

            <button
                onclick="changeDividendPeriod('1Y')"
                id="btnDiv1Y"
                class="px-3
                       py-1.5
                       rounded-lg
                       text-xs
                       font-bold
                       bg-blue-600
                       text-white">

                1Y

            </button>

            <button
                onclick="changeDividendPeriod('3Y')"
                id="btnDiv3Y"
                class="px-3
                       py-1.5
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                3Y

            </button>

            <button
                onclick="changeDividendPeriod('5Y')"
                id="btnDiv5Y"
                class="px-3
                       py-1.5
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                5Y

            </button>

            <button
                onclick="changeDividendPeriod('10Y')"
                id="btnDiv10Y"
                class="px-3
                       py-1.5
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                10Y

            </button>

        </div>

    </div>

    <div class="h-[360px]">

        <canvas id="dividendChart"></canvas>

    </div>

</div>


<!-- ====================================================== -->
<!-- 기간별 배당 바닥 가격 -->
<!-- ====================================================== -->

<div class="bg-slate-900/80
            p-5
            rounded-2xl
            border border-slate-800">

    <div class="mb-4">

        <h2 class="text-lg
                   font-black
                   text-white">

            🎯 기간별 배당 안전마진

        </h2>

        <p class="text-xs
                  text-slate-500
                  mt-1">

            역사적 최대 배당수익률을 기준으로 계산한
            기간별 바닥 가격

        </p>

    </div>

    <div class="grid
                grid-cols-1
                md:grid-cols-2
                xl:grid-cols-4
                gap-3">

        {matrix_cards}

    </div>

</div>


<!-- ====================================================== -->
<!-- 성장 차트 -->
<!-- ====================================================== -->

<div class="bg-slate-900/80
            p-5
            rounded-2xl
            border border-slate-800">

    <div class="flex
                items-center
                justify-between
                flex-wrap
                gap-3
                mb-4">

        <div>

            <h2 class="text-lg
                       font-black
                       text-white">

                📈 가격 / 성장률 분석

            </h2>

            <p class="text-xs
                      text-slate-500
                      mt-1">

                가격과 성장 관련 지표를 함께 확인합니다.

            </p>

        </div>

        <div class="flex
                    items-center
                    gap-2
                    flex-wrap">

            <button
                onclick="changeFinPeriod('1Y')"
                id="btnFin1Y"
                class="px-2.5
                       py-1
                       rounded-lg
                       text-xs
                       font-bold
                       bg-blue-600
                       text-white">

                1Y

            </button>

            <button
                onclick="changeFinPeriod('3Y')"
                id="btnFin3Y"
                class="px-2.5
                       py-1
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                3Y

            </button>

            <button
                onclick="changeFinPeriod('5Y')"
                id="btnFin5Y"
                class="px-2.5
                       py-1
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                5Y

            </button>

            <button
                onclick="changeFinPeriod('10Y')"
                id="btnFin10Y"
                class="px-2.5
                       py-1
                       rounded-lg
                       text-xs
                       font-semibold
                       bg-slate-800
                       text-slate-400">

                10Y

            </button>

        </div>

    </div>

    <div class="flex
                flex-wrap
                gap-3
                mb-3">

        <label class="text-xs
                      text-slate-300">

            <input
                type="checkbox"
                id="chkGrowthPrice"
                checked
                onchange="toggleGrowthLayers()">

            가격

        </label>

        <label class="text-xs
                      text-slate-300">

            <input
                type="checkbox"
                id="chkGrowthYoY"
                checked
                onchange="toggleGrowthLayers()">

            배당률

        </label>

        <label class="text-xs
                      text-slate-300">

            <input
                type="checkbox"
                id="chkGrowthOpm"
                checked
                onchange="toggleGrowthLayers()">

            RSI

        </label>

    </div>

    <div class="h-[360px]">

        <canvas id="growthChart"></canvas>

    </div>

</div>


<!-- ====================================================== -->
<!-- 뉴스 / 공시 -->
<!-- ====================================================== -->

<div class="grid
            grid-cols-1
            lg:grid-cols-2
            gap-4">


    <div class="bg-slate-900/80
                p-5
                rounded-2xl
                border
                border-slate-800">

        <div class="flex
                    items-center
                    justify-between
                    mb-3">

            <h2 class="text-lg
                       font-black
                       text-white">

                📰 최근 뉴스

            </h2>

        </div>

        <div class="flex-1
                    overflow-y-auto
                    space-y-2
                    pt-2.5
                    pr-1
                    custom-scroll
                    max-h-[500px]">

            {news_html}

        </div>

    </div>


    <div class="bg-slate-900/80
                p-5
                rounded-2xl
                border
                border-slate-800">

        <div class="flex
                    items-center
                    justify-between
                    mb-3">

            <h2 class="text-lg
                       font-black
                       text-white">

                📢 최근 공시

            </h2>

        </div>

        <div class="flex-1
                    overflow-y-auto
                    space-y-2
                    pt-2.5
                    pr-1
                    custom-scroll
                    max-h-[500px]">

            {notice_html}

        </div>

    </div>

</div>


<!-- ====================================================== -->
<!-- 하단 정보 -->
<!-- ====================================================== -->

<div class="bg-slate-900/60
            p-4
            rounded-xl
            border
            border-slate-800">

    <div class="grid
                grid-cols-1
                md:grid-cols-4
                gap-3">

        <div>

            <p class="text-[10px]
                      text-slate-500">

                현재 DPS

            </p>

            <p class="text-sm
                      font-black
                      text-white">

                {data['current_dps']:,.0f}원

            </p>

        </div>

        <div>

            <p class="text-[10px]
                      text-slate-500">

                성장률

            </p>

            <p class="text-sm
                      font-black
                      text-white">

                {gm.get('growth_rate', 0):.2f}%

            </p>

        </div>

        <div>

            <p class="text-[10px]
                      text-slate-500">

                성장 적정가

            </p>

            <p class="text-sm
                      font-black
                      text-white">

                {peg_fair:,.0f}원

            </p>

        </div>

        <div>

            <p class="text-[10px]
                      text-slate-500">

                성장 하단가

            </p>

            <p class="text-sm
                      font-black
                      text-white">

                {peg_bottom:,.0f}원

            </p>

        </div>

    </div>

</div>


</div>


<script>

/* ========================================================
   Python 데이터
   ======================================================== */

const chartPayload =
    {chart_payload_json};

let dividendChart = null;
let growthChart = null;

let currentDividendPeriod = "1Y";
let curFinPeriod = "1Y";


/* ========================================================
   기간 필터
   ======================================================== */

function getFilteredData(period) {{

    const years =
        period === "1Y" ? 1 :
        period === "3Y" ? 3 :
        period === "5Y" ? 5 :
        10;

    const total =
        chartPayload.dates.length;

    const count = Math.min(
        total,
        Math.ceil(
            years * 26
        )
    );

    const start =
        Math.max(
            0,
            total - count
        );

    return {{
        dates:
            chartPayload.dates.slice(
                start
            ),

        prices:
            chartPayload.prices.slice(
                start
            ),

        yields:
            chartPayload.yields.slice(
                start
            ),

        rsis:
            chartPayload.rsis.slice(
                start
            )
    }};
}}


/* ========================================================
   배당 차트
   ======================================================== */

function renderDividendChart() {{

    const d =
        getFilteredData(
            currentDividendPeriod
        );

    const ctx =
        document
        .getElementById(
            "dividendChart"
        )
        .getContext("2d");

    if (dividendChart) {{
        dividendChart.destroy();
    }}

    dividendChart =
        new Chart(
            ctx,
            {{

                type: "line",

                data: {{

                    labels:
                        d.dates,

                    datasets: [

                        {{
                            label:
                                "배당수익률",

                            data:
                                d.yields,

                            borderWidth:
                                2,

                            pointRadius:
                                0,

                            tension:
                                0.2,

                            yAxisID:
                                "y"
                        }}

                    ]

                }},

                options: {{

                    responsive:
                        true,

                    maintainAspectRatio:
                        false,

                    interaction: {{
                        mode:
                            "index",
                        intersect:
                            false
                    }},

                    plugins: {{

                        legend: {{
                            labels: {{
                                color:
                                    "#cbd5e1"
                            }}
                        }}

                    }},

                    scales: {{

                        x: {{
                            ticks: {{
                                color:
                                    "#64748b",
                                maxTicksLimit:
                                    10
                            }},

                            grid: {{
                                color:
                                    "rgba(100,116,139,0.12)"
                            }}
                        }},

                        y: {{

                            position:
                                "left",

                            ticks: {{
                                color:
                                    "#60a5fa",

                                callback:
                                    function(value) {{
                                        return value
                                            + "%";
                                    }}
                            }},

                            grid: {{
                                color:
                                    "rgba(100,116,139,0.12)"
                            }}

                        }}

                    }}

                }}

            }}
        );
}}


/* ========================================================
   배당 기간 변경
   ======================================================== */

function changeDividendPeriod(period) {{

    currentDividendPeriod =
        period;

    [
        "1Y",
        "3Y",
        "5Y",
        "10Y"
    ].forEach(
        function(p) {{

            const btn =
                document.getElementById(
                    "btnDiv" + p
                );

            if (!btn)
                return;

            if (p === period) {{

                btn.className =
                    "px-3 py-1.5 rounded-lg "
                    + "text-xs font-bold "
                    + "bg-blue-600 "
                    + "text-white";

            }} else {{

                btn.className =
                    "px-3 py-1.5 rounded-lg "
                    + "text-xs font-semibold "
                    + "bg-slate-800 "
                    + "text-slate-400";

            }}

        }
    );

    renderDividendChart();
}}


/* ========================================================
   성장 차트
   ======================================================== */

function renderGrowthChart() {{

    const d =
        getFilteredData(
            curFinPeriod
        );

    const ctx =
        document
        .getElementById(
            "growthChart"
        )
        .getContext("2d");

    if (growthChart) {{
        growthChart.destroy();
    }}

    growthChart =
        new Chart(
            ctx,
            {{

                type:
                    "line",

                data: {{

                    labels:
                        d.dates,

                    datasets: [

                        {{
                            label:
                                "가격",

                            data:
                                d.prices,

                            borderWidth:
                                2,

                            pointRadius:
                                0,

                            tension:
                                0.2,

                            yAxisID:
                                "y_price"
                        }},

                        {{
                            label:
                                "배당수익률",

                            data:
                                d.yields,

                            borderWidth:
                                2,

                            pointRadius:
                                0,

                            tension:
                                0.2,

                            yAxisID:
                                "y_growth"
                        }},

                        {{
                            label:
                                "RSI",

                            data:
                                d.rsis,

                            borderWidth:
                                2,

                            pointRadius:
                                0,

                            tension:
                                0.2,

                            yAxisID:
                                "y_growth"
                        }},

                        {{
                            label:
                                "성장률 기준선",

                            data:
                                d.dates.map(
                                    () => {gm.get('growth_rate', 7.0)}
                                ),

                            borderWidth:
                                1,

                            pointRadius:
                                0,

                            borderDash:
                                [5,5],

                            yAxisID:
                                "y_growth"
                        }}

                    ]

                }},

                options: {{

                    responsive:
                        true,

                    maintainAspectRatio:
                        false,

                    interaction: {{
                        mode:
                            "index",
                        intersect:
                            false
                    }},

                    plugins: {{

                        legend: {{
                            labels: {{
                                color:
                                    "#cbd5e1"
                            }}
                        }}

                    }},

                    scales: {{

                        y_price: {{

                            type:
                                "linear",

                            position:
                                "left",

                            display:
                                true,

                            ticks: {{
                                color:
                                    "#f8fafc"
                            }},

                            grid: {{
                                color:
                                    "rgba(100,116,139,0.12)"
                            }}

                        }},

                        y_growth: {{

                            type:
                                "linear",

                            position:
                                "right",

                            display:
                                true,

                            ticks: {{
                                color:
                                    "#60a5fa"
                            }},

                            grid: {{
                                drawOnChartArea:
                                    false
                            }}

                        }},

                        x: {{

                            ticks: {{
                                color:
                                    "#64748b",

                                maxTicksLimit:
                                    10
                            }},

                            grid: {{
                                color:
                                    "rgba(100,116,139,0.12)"
                            }}

                        }}

                    }}

                }}

            }}
        );
}}


/* ========================================================
   재무 기간 변경
   ======================================================== */

function changeFinPeriod(period) {{

    curFinPeriod =
        period;

    [
        "1Y",
        "3Y",
        "5Y",
        "10Y"
    ].forEach(
        function(p) {{

            const btn =
                document.getElementById(
                    "btnFin" + p
                );

            if (!btn)
                return;

            if (p === period) {{

                btn.className =
                    "px-2.5 py-1 "
                    + "rounded-lg text-xs "
                    + "font-bold "
                    + "bg-blue-600 "
                    + "text-white";

            }} else {{

                btn.className =
                    "px-2.5 py-1 "
                    + "rounded-lg text-xs "
                    + "font-semibold "
                    + "bg-slate-800 "
                    + "text-slate-400";

            }}

        }
    );

    renderGrowthChart();
}}


/* ========================================================
   성장 레이어 토글
   ======================================================== */

function toggleGrowthLayers() {{

    if (!growthChart)
        return;

    const price =
        document.getElementById(
            "chkGrowthPrice"
        );

    const yieldBox =
        document.getElementById(
            "chkGrowthYoY"
        );

    const rsi =
        document.getElementById(
            "chkGrowthOpm"
        );

    growthChart
        .data
        .datasets[0]
        .hidden =
            !price.checked;

    growthChart
        .data
        .datasets[1]
        .hidden =
            !yieldBox.checked;

    growthChart
        .data
        .datasets[2]
        .hidden =
            !rsi.checked;

    growthChart
        .options
        .scales
        .y_price
        .display =
            price.checked;

    growthChart
        .options
        .scales
        .y_growth
        .display =
            yieldBox.checked ||
            rsi.checked;

    growthChart.update();
}}


/* ========================================================
   초기 실행
   ======================================================== */

renderDividendChart();

renderGrowthChart();

</script>

</body>

</html>
"""

    # --------------------------------------------------------
    # HTML 저장
    # --------------------------------------------------------

    try:

        with open(
            file_name,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                html_content
            )

    except Exception as e:

        logging.warning(
            f"HTML 파일 저장 실패: {e}"
        )

    print(
        f"✅ [{data['name']}] "
        f"v39.5 대시보드 렌더링 완료!"
    )

    return html_content


# ============================================================
# Streamlit UI
# ============================================================

st.title(
    "📊 종목별 맞춤형 동적 가중치 대시보드"
)

st.markdown(
    """
### 🔎 종목 검색

종목명 전체 또는 일부 단어를 입력하세요.

예:

- `부동산`
- `리츠`
- `삼성`
- `하이닉스`
- `반도체`
- `커버드콜`
- `005930`

**검색어가 종목명에 포함된 모든 종목을 검색합니다.**
"""
)


# ============================================================
# 검색창
# ============================================================

user_query = st.text_input(
    "🔎 종목 검색",
    value="",
    placeholder=(
        "예: 부동산 / 리츠 / 삼성 / "
        "하이닉스 / 반도체 / 005930"
    )
).strip()


# ============================================================
# 검색 실행
# ============================================================

if user_query:

    selected_code = None
    selected_name = None

    # ========================================================
    # 1. 6자리 종목코드 검색
    # ========================================================

    if (
        user_query.isdigit()
        and len(user_query) <= 6
    ):

        results = search_stock_list(
            user_query,
            max_results=20
        )

        if results:

            st.subheader(
                f"🔍 종목코드 검색 결과 "
                f"({len(results)}개)"
            )

            options = [
                f"{x['name']}  |  {x['code']}"
                for x in results
            ]

            selected_option = st.selectbox(
                "📌 분석할 종목 선택",
                options,
                key="stock_selector_code"
            )

            selected_idx = (
                options.index(
                    selected_option
                )
            )

            selected_code = (
                results[selected_idx]["code"]
            )

            selected_name = (
                results[selected_idx]["name"]
            )

        else:

            # ------------------------------------------------
            # 네이버 직접 확인
            # ------------------------------------------------

            selected_code = (
                user_query.zfill(6)
            )

            try:

                url = (
                    "https://finance.naver.com/"
                    f"item/main.naver?code="
                    f"{selected_code}"
                )

                res = requests.get(
                    url,
                    headers={
                        "User-Agent":
                            "Mozilla/5.0"
                    },
                    timeout=5
                )

                res.encoding = "cp949"

                soup = BeautifulSoup(
                    res.text,
                    "html.parser"
                )

                name_elem = (
                    soup.select_one(
                        ".wrap_company h2 a"
                    )
                )

                if name_elem:

                    selected_name = (
                        name_elem
                        .text
                        .strip()
                    )

            except Exception:
                selected_name = None

            if not selected_name:

                st.error(
                    "❌ 해당 종목코드를 "
                    "찾지 못했습니다.\n\n"
                    "종목코드를 확인해주세요."
                )

    # ========================================================
    # 2. 종목명 / 부분검색
    # ========================================================

    else:

        search_results = search_stock_list(
            user_query,
            max_results=200
        )

        # ----------------------------------------------------
        # 검색 결과 없음
        # ----------------------------------------------------

        if not search_results:

            st.warning(
                f"⚠️ '{user_query}'가 "
                "종목명에 포함된 종목을 "
                "찾지 못했습니다."
            )

            st.info(
                """
                💡 검색 팁

                종목명의 일부 단어만 입력해보세요.

                예:
                `부동산`
                `리츠`
                `삼성`
                `하이닉스`
                `반도체`
                """
            )

        # ----------------------------------------------------
        # 검색 결과 있음
        # ----------------------------------------------------

        else:

            st.subheader(
                f"🔍 '{user_query}' "
                f"검색 결과 "
                f"({len(search_results)}개)"
            )

            st.caption(
                "종목명에 검색어가 포함된 "
                "종목을 검색했습니다. "
                "아래 목록에서 분석할 종목을 선택하세요."
            )

            # ------------------------------------------------
            # SelectBox
            # ------------------------------------------------

            options = [
                f"{item['name']}  |  {item['code']}"
                for item in search_results
            ]

            selected_option = st.selectbox(
                "📌 분석할 종목 선택",
                options,
                key=f"stock_selector_{user_query}"
            )

            selected_idx = (
                options.index(
                    selected_option
                )
            )

            selected_code = (
                search_results[
                    selected_idx
                ]["code"]
            )

            selected_name = (
                search_results[
                    selected_idx
                ]["name"]
            )

            # ------------------------------------------------
            # 선택된 종목 표시
            # ------------------------------------------------

            st.success(
                f"선택된 종목: "
                f"**{selected_name} "
                f"({selected_code})**"
            )

            # ------------------------------------------------
            # 검색 결과 테이블
            # ------------------------------------------------

            result_table = pd.DataFrame(
                search_results
            )

            result_table = result_table[
                ["code", "name"]
            ].copy()

            result_table.columns = [
                "종목코드",
                "종목명"
            ]

            st.dataframe(
                result_table,
                use_container_width=True,
                hide_index=True,
                height=min(
                    500,
                    35 * len(
                        result_table
                    ) + 45
                )
            )


    # ========================================================
    # 3. 분석 실행
    # ========================================================

    if (
        selected_code
        and selected_name
    ):

        st.divider()

        st.markdown(
            f"""
            ### 📌 분석 대상

            **{selected_name}**
            `{selected_code}`
            """
        )

        run_analysis = st.button(
            "🚀 선택 종목 분석 실행",
            type="primary",
            use_container_width=True
        )

        if run_analysis:

            with st.spinner(
                f"'{selected_name}' "
                "데이터를 수집하고 "
                "대시보드를 생성하는 중입니다..."
            ):

                try:

                    html_str = (
                        generate_v39_dashboard(
                            query=user_query,
                            code=selected_code,
                            name=selected_name
                        )
                    )

                    if html_str:

                        st.success(
                            f"✅ "
                            f"{selected_name} "
                            "분석 완료"
                        )

                        components.html(
                            html_str,
                            height=1600,
                            scrolling=True
                        )

                    else:

                        st.error(
                            "❌ 대시보드 생성에 "
                            "실패했습니다."
                        )

                except Exception as e:

                    logging.exception(
                        "대시보드 생성 오류"
                    )

                    st.error(
                        f"❌ 분석 중 오류가 "
                        f"발생했습니다: {str(e)}"
                    )


# ============================================================
# 검색창이 비어 있을 때
# ============================================================

else:

    st.info(
        """
        👆 위 검색창에 종목명이나 일부 단어를 입력하세요.

        예를 들어 **부동산**이라고 입력하면
        종목명에 **부동산**이 포함된 종목을
        모두 검색해서 선택할 수 있습니다.
        """
    )
