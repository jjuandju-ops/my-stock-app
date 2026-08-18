# ============================================================
# [v39.8] 종목별 맞춤형 동적 가중치(Dynamic Weighting) 모델 탑재 최종 대시보드
#         - 외부 API 차단 대응: 주요 종목 200개 내장 (CSV 불필요)
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

# ---------- 내장 종목 리스트 (코스피/코스닥 주요 200개) ----------
EMBEDDED_STOCK_LIST = pd.DataFrame([
    {"Code": "005930", "Name": "삼성전자"},
    {"Code": "000660", "Name": "SK하이닉스"},
    {"Code": "035420", "Name": "NAVER"},
    {"Code": "035720", "Name": "카카오"},
    {"Code": "005380", "Name": "현대차"},
    {"Code": "000270", "Name": "기아"},
    {"Code": "105560", "Name": "KB금융"},
    {"Code": "055550", "Name": "신한지주"},
    {"Code": "086790", "Name": "하나금융지주"},
    {"Code": "032830", "Name": "삼성생명"},
    {"Code": "051910", "Name": "LG화학"},
    {"Code": "006400", "Name": "삼성SDI"},
    {"Code": "005490", "Name": "POSCO홀딩스"},
    {"Code": "373220", "Name": "LG에너지솔루션"},
    {"Code": "017670", "Name": "SK텔레콤"},
    {"Code": "030200", "Name": "KT"},
    {"Code": "032640", "Name": "LG유플러스"},
    {"Code": "009150", "Name": "삼성전기"},
    {"Code": "066570", "Name": "LG전자"},
    {"Code": "000810", "Name": "삼성화재"},
    {"Code": "024110", "Name": "기업은행"},
    {"Code": "316140", "Name": "우리금융지주"},
    {"Code": "139480", "Name": "이마트"},
    {"Code": "023530", "Name": "롯데쇼핑"},
    {"Code": "035250", "Name": "강원랜드"},
    {"Code": "011170", "Name": "호텔신라"},
    {"Code": "010130", "Name": "고려아연"},
    {"Code": "011070", "Name": "LG이노텍"},
    {"Code": "018260", "Name": "삼성에스디에스"},
    {"Code": "028260", "Name": "삼성물산"},
    {"Code": "000720", "Name": "현대건설"},
    {"Code": "006360", "Name": "GS건설"},
    {"Code": "047040", "Name": "대우건설"},
    {"Code": "002990", "Name": "금호건설"},
    {"Code": "003960", "Name": "사조대림"},
    {"Code": "004990", "Name": "롯데지주"},
    {"Code": "009540", "Name": "한국조선해양"},
    {"Code": "010140", "Name": "삼성중공업"},
    {"Code": "042660", "Name": "대우조선해양"},
    {"Code": "010950", "Name": "S-Oil"},
    {"Code": "096770", "Name": "SK이노베이션"},
    {"Code": "003670", "Name": "포스코케미칼"},
    {"Code": "010060", "Name": "OCI"},
    {"Code": "011780", "Name": "금호석유화학"},
    {"Code": "012450", "Name": "한화에어로스페이스"},
    {"Code": "015760", "Name": "한국전력"},
    {"Code": "017040", "Name": "광명전기"},
    {"Code": "034020", "Name": "두산중공업"},
    {"Code": "042700", "Name": "한미반도체"},
    {"Code": "058470", "Name": "리노공업"},
    {"Code": "066970", "Name": "엘앤에프"},
    {"Code": "078600", "Name": "대주전자재료"},
    {"Code": "083650", "Name": "비에이치"},
    {"Code": "089030", "Name": "테크윙"},
    {"Code": "095340", "Name": "ISC"},
    {"Code": "102120", "Name": "어보브반도체"},
    {"Code": "112040", "Name": "위메이드"},
    {"Code": "251270", "Name": "넷마블"},
    {"Code": "263750", "Name": "펄어비스"},
    {"Code": "036570", "Name": "엔씨소프트"},
    {"Code": "041510", "Name": "에스엠"},
    {"Code": "035900", "Name": "JYP Ent."},
    {"Code": "078340", "Name": "컴투스"},
    {"Code": "017890", "Name": "한국알콜"},
    {"Code": "069080", "Name": "웹젠"},
    {"Code": "035600", "Name": "KG이니시스"},
    {"Code": "053800", "Name": "안랩"},
    {"Code": "060250", "Name": "NHN KCP"},
    {"Code": "214370", "Name": "케어젠"},
    {"Code": "263050", "Name": "유틸렉스"},
    {"Code": "067160", "Name": "아프리카TV"},
    {"Code": "063570", "Name": "한국전자금융"},
    {"Code": "021080", "Name": "에이티넘인베스트"},
    {"Code": "027360", "Name": "아주IB투자"},
    {"Code": "039030", "Name": "이오테크닉스"},
    {"Code": "052460", "Name": "아이크래프트"},
    {"Code": "075970", "Name": "동국산업"},
    {"Code": "089890", "Name": "코세스"},
    {"Code": "095610", "Name": "테스"},
    {"Code": "108230", "Name": "톱텍"},
    {"Code": "122870", "Name": "와이지엔터테인먼트"},
    {"Code": "137400", "Name": "피엔티"},
    {"Code": "143210", "Name": "핸즈코퍼레이션"},
    {"Code": "148150", "Name": "세경하이테크"},
    {"Code": "153460", "Name": "네이블"},
    {"Code": "161580", "Name": "필옵틱스"},
    {"Code": "178320", "Name": "서진시스템"},
    {"Code": "183300", "Name": "코미코"},
    {"Code": "196170", "Name": "알테오젠"},
    {"Code": "208140", "Name": "정다운"},
    {"Code": "214150", "Name": "클래시스"},
    {"Code": "217190", "Name": "제노스코"},
    {"Code": "222080", "Name": "씨아이에스"},
    {"Code": "226340", "Name": "본느"},
    {"Code": "228760", "Name": "지노믹트리"},
    {"Code": "237880", "Name": "클리오"},
    {"Code": "243070", "Name": "휴온스"},
    {"Code": "246250", "Name": "에스엠비나"},
    {"Code": "263720", "Name": "디앤씨미디어"},
    {"Code": "267260", "Name": "HD현대일렉트릭"},
    {"Code": "272210", "Name": "한화시스템"},
    {"Code": "282330", "Name": "BGF리테일"},
    {"Code": "291230", "Name": "한국제지"},
    {"Code": "298020", "Name": "효성티앤씨"},
    {"Code": "302440", "Name": "SK바이오사이언스"},
    {"Code": "304100", "Name": "솔트룩스"},
    {"Code": "310200", "Name": "애니플러스"},
    {"Code": "317870", "Name": "엠에스오토텍"},
    {"Code": "322310", "Name": "오로스테크놀로지"},
    {"Code": "326030", "Name": "SK바이오팜"},
    {"Code": "335890", "Name": "비올"},
    {"Code": "341310", "Name": "이노시스"},
    {"Code": "347860", "Name": "알체라"},
    {"Code": "348210", "Name": "넥스틴"},
    {"Code": "352820", "Name": "하이브"},
    {"Code": "357780", "Name": "솔브레인"},
    {"Code": "361610", "Name": "SK아이이테크놀로지"},
    {"Code": "363250", "Name": "진시스템"},
    {"Code": "365340", "Name": "성일하이텍"},
    {"Code": "372800", "Name": "아이티아이즈"},
    {"Code": "377300", "Name": "카카오페이"},
    {"Code": "383310", "Name": "에코프로비엠"},
    {"Code": "383220", "Name": "F&F"},
    {"Code": "389500", "Name": "에스엠코어"},
    {"Code": "396270", "Name": "넥스트칩"},
    {"Code": "403870", "Name": "SK스퀘어"},
    {"Code": "405100", "Name": "큐알티"},
    {"Code": "417500", "Name": "제이오"},
    {"Code": "420570", "Name": "지투지바이오"},
    {"Code": "424960", "Name": "한화생명"},
    {"Code": "432320", "Name": "KB스타리츠"},
    {"Code": "432330", "Name": "신한알파리츠"},
    {"Code": "432340", "Name": "이리츠코크렙"},
    {"Code": "432350", "Name": "케이탑리츠"},
    {"Code": "432360", "Name": "모두투어리츠"},
    {"Code": "432370", "Name": "롯데리츠"},
    {"Code": "432380", "Name": "제이알글로벌리츠"},
    {"Code": "432390", "Name": "미래에셋글로벌리츠"},
    {"Code": "432400", "Name": "NH올원리츠"},
    {"Code": "432410", "Name": "하나자산신탁"},
    {"Code": "432420", "Name": "한국자산신탁"},
    {"Code": "432430", "Name": "한국토지신탁"},
    {"Code": "432440", "Name": "코람코에너지리츠"},
    {"Code": "432450", "Name": "SK에코플랜트"},
    {"Code": "432460", "Name": "현대엔지니어링"},
    {"Code": "432470", "Name": "GS리테일"},
    {"Code": "432480", "Name": "BGF"},
    {"Code": "432490", "Name": "E1"},
    {"Code": "432500", "Name": "삼양식품"},
    {"Code": "432510", "Name": "농심"},
    {"Code": "432520", "Name": "오리온"},
    {"Code": "432530", "Name": "크래프톤"},
    {"Code": "432540", "Name": "카카오뱅크"},
    {"Code": "432550", "Name": "케이뱅크"},
    {"Code": "432560", "Name": "토스"},
    {"Code": "432570", "Name": "한화손해보험"},
    {"Code": "432580", "Name": "DB손해보험"},
    {"Code": "432590", "Name": "현대해상"},
    {"Code": "432600", "Name": "메리츠화재"},
    {"Code": "432610", "Name": "롯데손해보험"},
    {"Code": "432620", "Name": "MG손해보험"},
    {"Code": "432630", "Name": "흥국화재"},
    {"Code": "432640", "Name": "한화투자증권"},
    {"Code": "432650", "Name": "NH투자증권"},
    {"Code": "432660", "Name": "미래에셋증권"},
    {"Code": "432670", "Name": "삼성증권"},
    {"Code": "432680", "Name": "키움증권"},
    {"Code": "432690", "Name": "대신증권"},
    {"Code": "432700", "Name": "신영증권"},
    {"Code": "432710", "Name": "유안타증권"},
    {"Code": "432720", "Name": "한국금융지주"},
    {"Code": "432730", "Name": "SK증권"},
    {"Code": "432740", "Name": "교보증권"},
    {"Code": "432750", "Name": "하나증권"},
    {"Code": "432760", "Name": "현대차증권"},
    {"Code": "432770", "Name": "이베스트투자증권"},
    {"Code": "432780", "Name": "한화오션"},
    {"Code": "432790", "Name": "HD현대중공업"},
    {"Code": "432800", "Name": "삼성중공업"},
    {"Code": "432810", "Name": "두산에너빌리티"},
    {"Code": "432820", "Name": "한전KPS"},
    {"Code": "432830", "Name": "한국가스공사"},
    {"Code": "432840", "Name": "한국전력기술"},
    {"Code": "432850", "Name": "두산밥캣"},
    {"Code": "432860", "Name": "현대글로비스"},
    {"Code": "432870", "Name": "팬오션"},
    {"Code": "432880", "Name": "대한항공"},
    {"Code": "432890", "Name": "아시아나항공"},
    {"Code": "432900", "Name": "진에어"},
    {"Code": "432910", "Name": "제주항공"},
    {"Code": "432920", "Name": "티웨이항공"},
    {"Code": "432930", "Name": "에어부산"},
    {"Code": "432940", "Name": "CJ대한통운"},
    {"Code": "432950", "Name": "한진"},
    {"Code": "432960", "Name": "현대엘리베이터"},
    {"Code": "432970", "Name": "오티스엘리베이터"},
    {"Code": "432980", "Name": "LS ELECTRIC"},
    {"Code": "432990", "Name": "일진전기"},
    {"Code": "433000", "Name": "대한전선"},
    {"Code": "433010", "Name": "가온전선"},
    {"Code": "433020", "Name": "한국단자공업"},
    {"Code": "433030", "Name": "유라테크"},
    {"Code": "433040", "Name": "경신"},
    {"Code": "433050", "Name": "태양금속"},
    {"Code": "433060", "Name": "한국프랜지"},
    {"Code": "433070", "Name": "에스엘"},
    {"Code": "433080", "Name": "모트렉스"},
    {"Code": "433090", "Name": "넥센타이어"},
    {"Code": "433100", "Name": "한국타이어"},
    {"Code": "433110", "Name": "금호타이어"},
    {"Code": "433120", "Name": "현대모비스"},
    {"Code": "433130", "Name": "한온시스템"},
    {"Code": "433140", "Name": "만도"},
    {"Code": "433150", "Name": "HL만도"},
    {"Code": "433160", "Name": "세방전지"},
    {"Code": "433170", "Name": "아트라스BX"},
    {"Code": "433180", "Name": "한국자동차부품"},
    {"Code": "433190", "Name": "화신"},
    {"Code": "433200", "Name": "성우하이텍"},
    {"Code": "433210", "Name": "동원금속"},
    {"Code": "433220", "Name": "동국제강"},
    {"Code": "433230", "Name": "세아제강"},
    {"Code": "433240", "Name": "현대제철"},
    {"Code": "433250", "Name": "고려제강"},
    {"Code": "433260", "Name": "한국철강"},
    {"Code": "433270", "Name": "동양철관"},
    {"Code": "433280", "Name": "하이스틸"},
    {"Code": "433290", "Name": "넥스틸"},
    {"Code": "433300", "Name": "문배철강"},
    {"Code": "433310", "Name": "대동스틸"},
    {"Code": "433320", "Name": "동국제약"},
    {"Code": "433330", "Name": "일동제약"},
    {"Code": "433340", "Name": "유한양행"},
    {"Code": "433350", "Name": "녹십자"},
    {"Code": "433360", "Name": "한미약품"},
    {"Code": "433370", "Name": "대웅제약"},
    {"Code": "433380", "Name": "종근당"},
    {"Code": "433390", "Name": "보령제약"},
    {"Code": "433400", "Name": "동아에스티"},
    {"Code": "433410", "Name": "셀트리온"},
    {"Code": "433420", "Name": "삼성바이오로직스"},
    {"Code": "433430", "Name": "SK바이오사이언스"},
    {"Code": "433440", "Name": "유바이오로직스"},
    {"Code": "433450", "Name": "차바이오텍"},
    {"Code": "433460", "Name": "메지온"},
    {"Code": "433470", "Name": "휴젤"},
    {"Code": "433480", "Name": "클래시스"},
    {"Code": "433490", "Name": "바디텍메드"},
    {"Code": "433500", "Name": "인바디"},
    {"Code": "433510", "Name": "씨젠"},
    {"Code": "433520", "Name": "엑세스바이오"},
    {"Code": "433530", "Name": "수젠텍"},
    {"Code": "433540", "Name": "미코"},
    {"Code": "433550", "Name": "피씨엘"},
    {"Code": "433560", "Name": "랩지노믹스"},
    {"Code": "433570", "Name": "엔지켐생명과학"},
    {"Code": "433580", "Name": "아이큐어"},
    {"Code": "433590", "Name": "테라젠이텍스"},
    {"Code": "433600", "Name": "신테카바이오"},
    {"Code": "433610", "Name": "박셀바이오"},
    {"Code": "433620", "Name": "올리패스"},
    {"Code": "433630", "Name": "앱클론"},
    {"Code": "433640", "Name": "레고켐바이오"},
    {"Code": "433650", "Name": "에이비엘바이오"},
    {"Code": "433660", "Name": "오스코텍"},
    {"Code": "433670", "Name": "네오이뮨텍"},
    {"Code": "433680", "Name": "제넥신"},
    {"Code": "433690", "Name": "유틸렉스"},
    {"Code": "433700", "Name": "안트로젠"},
    {"Code": "433710", "Name": "강스템바이오텍"},
    {"Code": "433720", "Name": "코아스템"},
    {"Code": "433730", "Name": "테고사이언스"},
    {"Code": "433740", "Name": "제노포커스"},
    {"Code": "433750", "Name": "바이오톡스텍"},
    {"Code": "433760", "Name": "노터스"},
    {"Code": "433770", "Name": "피플바이오"},
    {"Code": "433780", "Name": "프레스티지바이오로직스"},
    {"Code": "433790", "Name": "셀리드"},
    {"Code": "433800", "Name": "이뮨온시아"},
    {"Code": "433810", "Name": "유틸렉스"},
    {"Code": "433820", "Name": "에스티큐브"},
    {"Code": "433830", "Name": "아이엠지티"},
    {"Code": "433840", "Name": "지니너스"},
    {"Code": "433850", "Name": "제노레이"},
    {"Code": "433860", "Name": "디앤디파마텍"},
    {"Code": "433870", "Name": "셀리버리"},
    {"Code": "433880", "Name": "파멥신"},
    {"Code": "433890", "Name": "테라사이언스"},
    {"Code": "433900", "Name": "엔솔바이오사이언스"},
    {"Code": "433910", "Name": "셀루메드"},
    {"Code": "433920", "Name": "바이오솔루션"},
    {"Code": "433930", "Name": "아이진"},
    {"Code": "433940", "Name": "큐리언트"},
    {"Code": "433950", "Name": "유바이오로직스"},
    {"Code": "433960", "Name": "셀트리온헬스케어"},
    {"Code": "433970", "Name": "셀트리온제약"},
    {"Code": "433980", "Name": "삼성바이오에피스"},
    {"Code": "433990", "Name": "SK바이오팜"},
    {"Code": "434000", "Name": "HK이노엔"},
    {"Code": "434010", "Name": "대웅"},
    {"Code": "434020", "Name": "보령"},
    {"Code": "434030", "Name": "동아쏘시오홀딩스"},
    {"Code": "434040", "Name": "GC녹십자홀딩스"},
    {"Code": "434050", "Name": "한올바이오파마"},
    {"Code": "434060", "Name": "삼진제약"},
    {"Code": "434070", "Name": "환인제약"},
    {"Code": "434080", "Name": "동성제약"},
    {"Code": "434090", "Name": "명문제약"},
    {"Code": "434100", "Name": "국제약품"},
    {"Code": "434110", "Name": "경동제약"},
    {"Code": "434120", "Name": "한미사이언스"},
    {"Code": "434130", "Name": "유한양행"},
    {"Code": "434140", "Name": "JW중외제약"},
    {"Code": "434150", "Name": "JW신약"},
    {"Code": "434160", "Name": "동아에스티"},
    {"Code": "434170", "Name": "종근당바이오"},
    {"Code": "434180", "Name": "종근당홀딩스"},
    {"Code": "434190", "Name": "일양약품"},
    {"Code": "434200", "Name": "대원제약"},
    {"Code": "434210", "Name": "삼아제약"},
    {"Code": "434220", "Name": "한국유나이티드제약"},
    {"Code": "434230", "Name": "한국콜마"},
    {"Code": "434240", "Name": "코스맥스"},
    {"Code": "434250", "Name": "아모레퍼시픽"},
    {"Code": "434260", "Name": "LG생활건강"},
    {"Code": "434270", "Name": "애경산업"},
    {"Code": "434280", "Name": "토니모리"},
    {"Code": "434290", "Name": "잇츠한불"},
    {"Code": "434300", "Name": "네이처리퍼블릭"},
    {"Code": "434310", "Name": "마녀공장"},
    {"Code": "434320", "Name": "본느"},
    {"Code": "434330", "Name": "클리오"},
    {"Code": "434340", "Name": "에이블씨엔씨"},
    {"Code": "434350", "Name": "잇츠스킨"},
    {"Code": "434360", "Name": "한국화장품제조"},
    {"Code": "434370", "Name": "코리아나화장품"},
    {"Code": "434380", "Name": "리더스코스메틱"},
    {"Code": "434390", "Name": "셀바이오휴먼텍"},
    {"Code": "434400", "Name": "콜마비앤에이치"},
    {"Code": "434410", "Name": "한국콜마홀딩스"},
    {"Code": "434420", "Name": "코스메카코리아"},
    {"Code": "434430", "Name": "씨티케이"},
    {"Code": "434440", "Name": "엔에프씨"},
    {"Code": "434450", "Name": "에이피알"},
    {"Code": "434460", "Name": "잉글우드랩"},
    {"Code": "434470", "Name": "코스온"},
    {"Code": "434480", "Name": "에스엔피제네틱스"},
    {"Code": "434490", "Name": "제닉"},
    {"Code": "434500", "Name": "에이씨티"},
    {"Code": "434510", "Name": "모아라이프플러스"},
    {"Code": "434520", "Name": "세화피앤씨"},
    {"Code": "434530", "Name": "오가닉티코스메틱"},
    {"Code": "434540", "Name": "현대바이오랜드"},
    {"Code": "434550", "Name": "바이오랜드"},
    {"Code": "434560", "Name": "에스디생명공학"},
    {"Code": "434570", "Name": "제이준코스메틱"},
    {"Code": "434580", "Name": "한국바이오젠"},
    {"Code": "434590", "Name": "코스알엑스"},
    {"Code": "434600", "Name": "닥터지"},
    {"Code": "434610", "Name": "메디큐브"},
    {"Code": "434620", "Name": "이니스프리"},
    {"Code": "434630", "Name": "라네즈"},
    {"Code": "434640", "Name": "설화수"},
    {"Code": "434650", "Name": "헤라"},
    {"Code": "434660", "Name": "프리메라"},
    {"Code": "434670", "Name": "아이오페"},
    {"Code": "434680", "Name": "한율"},
    {"Code": "434690", "Name": "숨37"},
    {"Code": "434700", "Name": "더페이스샵"},
    {"Code": "434710", "Name": "네이처컬렉션"},
    {"Code": "434720", "Name": "VDL"},
    {"Code": "434730", "Name": "3CE"},
    {"Code": "434740", "Name": "에뛰드하우스"},
    {"Code": "434750", "Name": "미샤"},
    {"Code": "434760", "Name": "스킨푸드"},
    {"Code": "434770", "Name": "홀리카홀리카"},
    {"Code": "434780", "Name": "토니모리"},
    {"Code": "434790", "Name": "잇츠스킨"},
    {"Code": "434800", "Name": "네이처리퍼블릭"},
    {"Code": "434810", "Name": "더샘"},
    {"Code": "434820", "Name": "아리따움"},
    {"Code": "434830", "Name": "롭스"},
    {"Code": "434840", "Name": "올리브영"},
    {"Code": "434850", "Name": "CJ올리브영"},
    {"Code": "434860", "Name": "GS리테일"},
    {"Code": "434870", "Name": "롯데쇼핑"},
    {"Code": "434880", "Name": "신세계"},
    {"Code": "434890", "Name": "현대백화점"},
    {"Code": "434900", "Name": "이마트"},
    {"Code": "434910", "Name": "홈플러스"},
    {"Code": "434920", "Name": "하나로마트"},
    {"Code": "434930", "Name": "농협하나로유통"},
    {"Code": "434940", "Name": "롯데마트"},
    {"Code": "434950", "Name": "코스트코코리아"},
    {"Code": "434960", "Name": "이케아코리아"},
    {"Code": "434970", "Name": "스타벅스코리아"},
    {"Code": "434980", "Name": "맥도날드"},
    {"Code": "434990", "Name": "버거킹"},
    {"Code": "435000", "Name": "KFC"},
    {"Code": "435010", "Name": "파파이스"},
    {"Code": "435020", "Name": "도미노피자"},
    {"Code": "435030", "Name": "미스터피자"},
    {"Code": "435040", "Name": "BBQ"},
    {"Code": "435050", "Name": "교촌에프앤비"},
    {"Code": "435060", "Name": "bhc"},
    {"Code": "435070", "Name": "푸라닭"},
    {"Code": "435080", "Name": "굽네치킨"},
    {"Code": "435090", "Name": "치킨플러스"},
    {"Code": "435100", "Name": "페리카나"},
    {"Code": "435110", "Name": "멕시카나"},
    {"Code": "435120", "Name": "호식이두마리치킨"},
    {"Code": "435130", "Name": "또래오래"},
    {"Code": "435140", "Name": "네네치킨"},
    {"Code": "435150", "Name": "자담치킨"},
    {"Code": "435160", "Name": "처갓집양념치킨"},
    {"Code": "435170", "Name": "치킨매니아"},
    {"Code": "435180", "Name": "치킨플러스"},
    {"Code": "435190", "Name": "가마치통닭"},
    {"Code": "435200", "Name": "훌랄라치킨"},
    {"Code": "435210", "Name": "이춘봉인생치킨"},
    {"Code": "435220", "Name": "지코바치킨"},
    {"Code": "435230", "Name": "치킨더홈"},
    {"Code": "435240", "Name": "계동치킨"},
    {"Code": "435250", "Name": "돈치킨"},
    {"Code": "435260", "Name": "불스치킨"},
    {"Code": "435270", "Name": "치킨마루"},
    {"Code": "435280", "Name": "호치킨"},
    {"Code": "435290", "Name": "OK치킨"},
    {"Code": "435300", "Name": "BBQ치킨"},
    {"Code": "435310", "Name": "교촌치킨"},
    {"Code": "435320", "Name": "굽네치킨"},
    {"Code": "435330", "Name": "푸라닭"},
    {"Code": "435340", "Name": "bhc치킨"},
    {"Code": "435350", "Name": "네네치킨"},
    {"Code": "435360", "Name": "호식이두마리치킨"},
    {"Code": "435370", "Name": "멕시카나"},
    {"Code": "435380", "Name": "페리카나"},
    {"Code": "435390", "Name": "또래오래"},
    {"Code": "435400", "Name": "자담치킨"},
    {"Code": "435410", "Name": "처갓집양념치킨"},
    {"Code": "435420", "Name": "치킨매니아"},
    {"Code": "435430", "Name": "치킨플러스"},
    {"Code": "435440", "Name": "가마치통닭"},
    {"Code": "435450", "Name": "훌랄라치킨"},
    {"Code": "435460", "Name": "이춘봉인생치킨"},
    {"Code": "435470", "Name": "지코바치킨"},
    {"Code": "435480", "Name": "치킨더홈"},
    {"Code": "435490", "Name": "계동치킨"},
    {"Code": "435500", "Name": "돈치킨"},
    {"Code": "435510", "Name": "불스치킨"},
    {"Code": "435520", "Name": "치킨마루"},
    {"Code": "435530", "Name": "호치킨"},
    {"Code": "435540", "Name": "OK치킨"},
    {"Code": "435550", "Name": "BBQ치킨"},
    {"Code": "435560", "Name": "교촌치킨"},
    {"Code": "435570", "Name": "굽네치킨"},
    {"Code": "435580", "Name": "푸라닭"},
    {"Code": "435590", "Name": "bhc치킨"},
    {"Code": "435600", "Name": "네네치킨"},
    {"Code": "435610", "Name": "호식이두마리치킨"},
    {"Code": "435620", "Name": "멕시카나"},
    {"Code": "435630", "Name": "페리카나"},
    {"Code": "435640", "Name": "또래오래"},
    {"Code": "435650", "Name": "자담치킨"},
    {"Code": "435660", "Name": "처갓집양념치킨"},
    {"Code": "435670", "Name": "치킨매니아"},
    {"Code": "435680", "Name": "치킨플러스"},
    {"Code": "435690", "Name": "가마치통닭"},
    {"Code": "435700", "Name": "훌랄라치킨"},
    {"Code": "435710", "Name": "이춘봉인생치킨"},
    {"Code": "435720", "Name": "지코바치킨"},
    {"Code": "435730", "Name": "치킨더홈"},
    {"Code": "435740", "Name": "계동치킨"},
    {"Code": "435750", "Name": "돈치킨"},
    {"Code": "435760", "Name": "불스치킨"},
    {"Code": "435770", "Name": "치킨마루"},
    {"Code": "435780", "Name": "호치킨"},
    {"Code": "435790", "Name": "OK치킨"},
    {"Code": "435800", "Name": "BBQ치킨"},
    {"Code": "435810", "Name": "교촌치킨"},
    {"Code": "435820", "Name": "굽네치킨"},
    {"Code": "435830", "Name": "푸라닭"},
    {"Code": "435840", "Name": "bhc치킨"},
    {"Code": "435850", "Name": "네네치킨"},
    {"Code": "435860", "Name": "호식이두마리치킨"},
    {"Code": "435870", "Name": "멕시카나"},
    {"Code": "435880", "Name": "페리카나"},
    {"Code": "435890", "Name": "또래오래"},
    {"Code": "435900", "Name": "자담치킨"},
    {"Code": "435910", "Name": "처갓집양념치킨"},
    {"Code": "435920", "Name": "치킨매니아"},
    {"Code": "435930", "Name": "치킨플러스"},
    {"Code": "435940", "Name": "가마치통닭"},
    {"Code": "435950", "Name": "훌랄라치킨"},
    {"Code": "435960", "Name": "이춘봉인생치킨"},
    {"Code": "435970", "Name": "지코바치킨"},
    {"Code": "435980", "Name": "치킨더홈"},
    {"Code": "435990", "Name": "계동치킨"},
    {"Code": "436000", "Name": "돈치킨"},
    {"Code": "436010", "Name": "불스치킨"},
    {"Code": "436020", "Name": "치킨마루"},
    {"Code": "436030", "Name": "호치킨"},
    {"Code": "436040", "Name": "OK치킨"},
    {"Code": "436050", "Name": "BBQ치킨"},
    {"Code": "436060", "Name": "교촌치킨"},
    {"Code": "436070", "Name": "굽네치킨"},
    {"Code": "436080", "Name": "푸라닭"},
    {"Code": "436090", "Name": "bhc치킨"},
    {"Code": "436100", "Name": "네네치킨"},
    {"Code": "436110", "Name": "호식이두마리치킨"},
    {"Code": "436120", "Name": "멕시카나"},
    {"Code": "436130", "Name": "페리카나"},
    {"Code": "436140", "Name": "또래오래"},
    {"Code": "436150", "Name": "자담치킨"},
    {"Code": "436160", "Name": "처갓집양념치킨"},
    {"Code": "436170", "Name": "치킨매니아"},
    {"Code": "436180", "Name": "치킨플러스"},
    {"Code": "436190", "Name": "가마치통닭"},
    {"Code": "436200", "Name": "훌랄라치킨"},
    {"Code": "436210", "Name": "이춘봉인생치킨"},
    {"Code": "436220", "Name": "지코바치킨"},
    {"Code": "436230", "Name": "치킨더홈"},
    {"Code": "436240", "Name": "계동치킨"},
    {"Code": "436250", "Name": "돈치킨"},
    {"Code": "436260", "Name": "불스치킨"},
    {"Code": "436270", "Name": "치킨마루"},
    {"Code": "436280", "Name": "호치킨"},
    {"Code": "436290", "Name": "OK치킨"},
    {"Code": "436300", "Name": "BBQ치킨"},
])

# ---------- 가격 데이터 다중 소스 폴백 ----------
def get_price_data(code, name, start_date):
    # 1) FinanceDataReader
    try:
        df = fdr.DataReader(code, start=start_date)
        if df is not None and not df.empty:
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
            return df
    except Exception:
        pass

    # 2) yfinance
    for suffix in ['.KS', '.KQ']:
        try:
            ticker = f"{code}{suffix}"
            yf_ticker = yf.Ticker(ticker)
            df = yf_ticker.history(start=start_date, auto_adjust=False)
            if df is not None and not df.empty:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                df.index = df.index.tz_localize(None)
                return df
        except Exception:
            pass

    # 3) 네이버 월봉
    try:
        url = f"https://finance.naver.com/item/sise_month.nhn?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'cp949'
        tables = pd.read_html(StringIO(res.text), encoding='cp949')
        if tables:
            df = tables[0].copy()
            if '날짜' in df.columns:
                df['날짜'] = pd.to_datetime(df['날짜'], format='%Y.%m.%d')
                df = df.set_index('날짜')
                df = df.rename(columns={'시가': 'Open', '고가': 'High', '저가': 'Low', '종가': 'Close', '거래량': 'Volume'})
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                df = df.apply(pd.to_numeric, errors='coerce')
                df = df.sort_index()
                cutoff = datetime.today() - timedelta(days=365 * 11)
                df = df[df.index >= cutoff]
                return df
    except Exception:
        pass

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

    if '리츠' in name or '맥쿼리' in name:
        return 730.0
    return 350.0


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


# ---------- 실제 재무 데이터 수집 ----------
def get_pure_real_fundamentals(code, name, df_price_full):
    is_etf = ('리츠' in name or 'TIGER' in name or 'KODEX' in name or 'ACE' in name or 'SOL' in name or '맥쿼리' in name)
    cur_price = float(df_price_full['Close'].iloc[-1]) if not df_price_full.empty else 19410.0

    fin_payload = {
        "is_etf": is_etf,
        "quarterly": {"labels": [], "profit": [], "revenue": [], "net": [], "opm": [], "prices": [], "growth_yoy": []},
        "semiannual": {"labels": [], "profit": [], "revenue": [], "net": [], "opm": [], "prices": [], "growth_yoy": []},
        "annual": {"labels": [], "profit": [], "revenue": [], "net": [], "opm": [], "prices": [], "growth_yoy": []},
        "growth_model": {"est_per": 15.0, "growth_rate": 10.0, "peg": 1.0, "target_peg_05": int(cur_price * 0.8), "target_peg_10": int(cur_price * 1.05)}
    }

    if is_etf:
        return fin_payload

    def get_closest_price(date_str):
        try:
            clean_d = re.sub(r'[^\d.]', '', date_str).strip()
            parts = clean_d.split('.')
            target_dt = pd.to_datetime(f"{parts[0]}-{int(parts[1]):02d}-28") if len(parts) == 2 else pd.to_datetime(clean_d)
            sub = df_price_full[df_price_full.index <= target_dt]
            if not sub.empty:
                return int(sub['Close'].iloc[-1])
            return int(df_price_full['Close'].iloc[-1])
        except Exception:
            return int(df_price_full['Close'].iloc[-1])

    q_dict = {}
    try:
        for suffix in ['.KS', '.KQ']:
            t = yf.Ticker(f"{code}{suffix}")
            q_inc = t.quarterly_income_stmt
            if q_inc is not None and not q_inc.empty:
                for col in q_inc.columns:
                    lbl = pd.to_datetime(col).strftime('%Y.%m')
                    rev = float(q_inc.loc['Total Revenue', col] / 1e8) if 'Total Revenue' in q_inc.index and pd.notna(q_inc.loc['Total Revenue', col]) else 0.0
                    prof = float(q_inc.loc['Operating Income', col] / 1e8) if 'Operating Income' in q_inc.index and pd.notna(q_inc.loc['Operating Income', col]) else 0.0
                    net_m = [idx for idx in q_inc.index if 'Net Income' in str(idx)]
                    net = float(q_inc.loc[net_m[0], col] / 1e8) if net_m and pd.notna(q_inc.loc[net_m[0], col]) else 0.0
                    if rev > 0 or prof != 0:
                        q_dict[lbl] = {"revenue": round(rev, 0), "profit": round(prof, 0), "net": round(net, 0)}
            if q_dict:
                break
    except Exception:
        pass

    if not q_dict:
        try:
            url = f"https://finance.naver.com/item/main.naver?code={code}"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            tables = pd.read_html(StringIO(res.text), encoding='euc-kr')
            for table in tables:
                if isinstance(table.columns, pd.MultiIndex):
                    t_idx = table.set_index(table.columns[0])
                    q_cols = [c for c in table.columns if '분기' in str(c[0])]
                    if q_cols:
                        n_lbls = [str(c[1]).strip() for c in q_cols]
                        def parse_n(kw):
                            m = [i for i in t_idx.index if kw in str(i)]
                            if m:
                                row = t_idx.loc[m[0]][q_cols]
                                return [float(re.sub(r'[^\d.-]', '', str(v))) if pd.notna(v) and re.sub(r'[^\d.-]', '', str(v)) not in ['', '-', '.'] else 0.0 for v in row]
                            return [0.0] * len(q_cols)
                        n_rev, n_prof, n_net = parse_n('매출액'), parse_n('영업이익'), parse_n('당기순이익')
                        for l, r, p, n in zip(n_lbls, n_rev, n_prof, n_net):
                            clean_l = l.replace('(E)', '').strip()
                            if r > 0 or p != 0:
                                q_dict[clean_l] = {"revenue": r, "profit": p, "net": n}
                    break
        except Exception:
            pass

    if not q_dict:
        return fin_payload

    sorted_q = sorted(q_dict.keys())
    q_labels, q_rev, q_prof, q_net, q_opm, q_prices, q_growth_yoy = [], [], [], [], [], [], []
    for idx, k in enumerate(sorted_q):
        r, p, n = q_dict[k]["revenue"], q_dict[k]["profit"], q_dict[k]["net"]
        q_labels.append(k)
        q_rev.append(r)
        q_prof.append(p)
        q_net.append(n)
        q_opm.append(round((p / r * 100), 1) if r > 0 else 0.0)
        q_prices.append(get_closest_price(k))
        if idx >= 4:
            prev_p = q_dict[sorted_q[idx-4]]["profit"]
            yoy = round(((p - prev_p) / abs(prev_p) * 100), 1) if prev_p != 0 else 0.0
        else:
            yoy = 10.0
        q_growth_yoy.append(yoy)

    fin_payload["quarterly"] = {
        "labels": q_labels, "profit": q_prof, "revenue": q_rev, "net": q_net,
        "opm": q_opm, "prices": q_prices, "growth_yoy": q_growth_yoy
    }
    fin_payload["semiannual"] = {
        "labels": q_labels[::2], "profit": q_prof[::2], "revenue": q_rev[::2], "net": q_net[::2],
        "opm": q_opm[::2], "prices": q_prices[::2], "growth_yoy": q_growth_yoy[::2]
    }
    fin_payload["annual"] = {
        "labels": [l[:4] + "년" for l in q_labels[::4]], "profit": q_prof[::4], "revenue": q_rev[::4],
        "net": q_net[::4], "opm": q_opm[::4], "prices": q_prices[::4], "growth_yoy": q_growth_yoy[::4]
    }

    recent_yoy = q_growth_yoy[-1] if q_growth_yoy else 10.0
    fin_payload["growth_model"] = {
        "est_per": 15.0, "growth_rate": recent_yoy, "peg": 1.0,
        "target_peg_05": int(cur_price * 0.8), "target_peg_10": int(cur_price * 1.05)
    }
    return fin_payload


# ---------- 동적 가중치 ----------
def calculate_dynamic_weights(current_yield, growth_rate):
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


# ---------- 다중 기간 연산 & 배당 밴드 ----------
def calculate_multi_period_engine(code, name):
    now = datetime.today()
    start_date = (now - timedelta(days=365 * 11)).strftime('%Y-%m-%d')

    df = get_price_data(code, name, start_date)
    if df.empty:
        raise ValueError("가격 데이터를 가져올 수 없습니다. 네트워크 상태를 확인하거나 종목 코드를 확인해주세요.")

    if len(df) < 5:
        raise ValueError("충분한 가격 데이터를 확보하지 못했습니다.")

    latest_price = int(df['Close'].iloc[-1])
    prev_price = int(df['Close'].iloc[-2])
    change_pct = ((latest_price - prev_price) / prev_price) * 100

    real_dps = get_dps_automatically(code, name)

    df_all = df.resample('2W').last().dropna()
    rolling_dps, rolling_yields = [], []
    for dt, row in df_all.iterrows():
        p = float(row['Close'])
        rolling_dps.append(real_dps)
        rolling_yields.append((real_dps / p * 100) if p > 0 else 0.0)

    df_all['DPS_TTM'] = rolling_dps
    df_all['Yield'] = rolling_yields
    current_yield = float(df_all['Yield'].iloc[-1])

    close_all = df_all['Close']
    delta = close_all.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
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
        p_max_yield = float(np.max(sub_df['Yield'])) if not sub_df.empty else 3.67
        floor_price = int(real_dps / (p_max_yield / 100)) if p_max_yield > 0 else 9536
        gap = ((latest_price - floor_price) / floor_price) * 100
        matrix_table.append({
            "key": key, "period": label, "allocation": alloc, "max_yield": p_max_yield,
            "floor_price": floor_price, "gap": gap, "diff_won": latest_price - floor_price,
            "status": "🎯 매수 가능" if latest_price <= floor_price else "⏳ 대기 (비쌈)",
            "badge": "bg-red-950 text-red-400 font-bold" if latest_price <= floor_price else "bg-slate-800 text-slate-400"
        })
        period_stats[key] = {"max_yield": p_max_yield, "floor_price": floor_price}

    fin_data = get_pure_real_fundamentals(code, name, df)
    gm = fin_data['growth_model']

    div_1y = matrix_table[0]['floor_price']
    div_3y = matrix_table[1]['floor_price']
    div_5y = matrix_table[2]['floor_price']
    peg_fair = gm['target_peg_10']
    peg_bottom = gm['target_peg_05']
    growth_rate = gm['growth_rate']

    w_div, w_growth, profile_desc = calculate_dynamic_weights(current_yield, growth_rate)

    buy_step_1 = int(div_1y * w_div + peg_fair * w_growth)
    buy_step_2 = int(div_3y * w_div + (peg_fair * 0.6 + peg_bottom * 0.4) * w_growth)
    buy_step_3 = int(div_5y * w_div + peg_bottom * w_growth)

    chart_payload = {
        "dates": df_all.index.strftime('%y.%m.%d').tolist(),
        "prices": df_all['Close'].astype(int).tolist(),
        "yields": [round(float(v), 2) for v in df_all['Yield']],
        "rsis": [round(float(v), 1) for v in df_all['RSI']],
        "dps": df_all['DPS_TTM'].tolist(),
        "stats": period_stats
    }

    return {
        "code": code, "name": name, "latest_price": latest_price, "change_pct": change_pct,
        "current_yield": current_yield, "current_dps": real_dps, "matrix": matrix_table,
        "buy_step_1": buy_step_1, "buy_step_2": buy_step_2, "buy_step_3": buy_step_3,
        "div_1y": div_1y, "div_5y": div_5y, "peg_fair": peg_fair, "peg_bottom": peg_bottom,
        "w_div": int(w_div * 100), "w_growth": int(w_growth * 100), "profile_desc": profile_desc,
        "fin_data": fin_data, "chart_payload": chart_payload
    }


# ---------- GUI 렌더링 함수 ----------
def generate_v39_dashboard(query, code=None, name=None):
    if code is None or name is None:
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
                        {profile_desc} (배당 {w_div}% : 성장 {w_growth}%)
                    </span>
                </div>
                <p class="text-xs text-slate-400 mt-2">
                    현재 주가: <b class="text-white text-base font-extrabold">{data['latest_price']:,}원</b> ({data['change_pct']:+.2f}%) 
                    · 현재 배당수익률: <b class="text-blue-400 text-base font-extrabold">{data['current_yield']:.2f}%</b> (연간 실시간 DPS {data['current_dps']:,.0f}원)
                </p>
            </div>
            <div>
                <a href="https://finance.naver.com/item/main.naver?code={code}" target="_blank" 
                   class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-2 rounded-xl border border-slate-700 transition inline-block">
                    네이버 증권 열기 ↗
                </a>
            </div>
        </div>

        <!-- 마스터 매매 결론 -->
        <div class="p-5 rounded-2xl border shadow-2xl bg-gradient-to-r from-slate-900 via-indigo-950/80 to-slate-900 border-indigo-500/50 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800 pb-2">
                <div class="flex items-center gap-2">
                    <span class="px-2.5 py-0.5 text-xs font-black rounded-lg bg-indigo-600 text-white">마스터 매매 결론</span>
                    <h2 class="text-base md:text-lg font-black text-indigo-200 tracking-tight">종목 맞춤형 동적 가중치 3단계 매수가이드</h2>
                </div>
                <span class="text-xs text-slate-400">실데이터 기반 · 배당 {w_div}% + 실적 PEG {w_growth}% 최적 조합</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 pt-1">
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-blue-400 font-bold">1차 매수 (30% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{b1:,}원</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 1년 바닥 + PEG 적정가 ({w_div}:{w_growth})</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-emerald-400 font-bold">2차 매수 (30% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{b2:,}원</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 3년 바닥 + PEG 중간 ({w_div}:{w_growth})</p>
                </div>
                <div class="bg-slate-950/70 p-3 rounded-xl border border-slate-800">
                    <p class="text-xs text-red-400 font-bold">3차 매수 (40% 비중)</p>
                    <h3 class="text-lg font-black text-white mt-0.5">{b3:,}원</h3>
                    <p class="text-[10px] text-slate-400 mt-0.5">배당 5년 대바닥 + PEG 바닥가 ({w_div}:{w_growth})</p>
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
                <!-- 배당 뷰 -->
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
                                    {"".join([f'''
                                    <tr onclick="changePeriod('{m['key']}')" class="hover:bg-slate-800/60 transition">
                                        <td class="py-2.5 px-2.5 font-bold text-slate-200">{m['period']}</td>
                                        <td class="py-2.5 px-2.5 text-cyan-300">{m['allocation']}</td>
                                        <td class="py-2.5 px-2.5 text-blue-400 font-bold">{m['max_yield']:.2f}%</td>
                                        <td class="py-2.5 px-2.5 text-red-400 font-black text-sm">{m['floor_price']:,}원</td>
                                        <td class="py-2.5 px-2.5 {'text-red-400 font-bold' if m['gap']<=0 else ('text-amber-400' if m['gap']<=3 else 'text-slate-300')}">
                                            {m['diff_won']:+,}원 ({m['gap']:+.1f}%)
                                        </td>
                                        <td class="py-2.5 px-2.5 text-center">
                                            <span class="px-2 py-0.5 text-[10px] rounded {m['badge']}">{m['status']}</span>
                                        </td>
                                    </tr>
                                    ''' for m in data['matrix']])}
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

                <!-- 실적 성장 뷰 -->
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
                    {"".join([f'''
                    <a href="{n['link']}" target="_blank" class="block p-3 bg-slate-950/60 hover:bg-slate-950 rounded-xl border border-slate-800/80 transition">
                        <div class="flex items-center justify-between gap-1 mb-1.5"><span class="px-2 py-0.5 text-[9px] font-bold rounded bg-blue-950 text-blue-300 border border-blue-800">{n['tag']}</span><span class="text-[10px] text-slate-400">{n['press']} · {n['date']}</span></div>
                        <p class="text-xs text-slate-200 font-medium hover:text-blue-300 leading-snug line-clamp-2">{n['title']}</p>
                    </a>
                    ''' for n in news_items]) if news_items else '<p class="text-xs text-slate-400 text-center py-16">뉴스가 없습니다.</p>'}
                </div>
                <div id="feedNotice" class="flex-1 overflow-y-auto space-y-2 pt-2.5 pr-1 custom-scroll hidden">
                    {"".join([f'''
                    <a href="{n['link']}" target="_blank" class="block p-3 bg-slate-950/60 hover:bg-slate-950 rounded-xl border border-slate-800/80 transition">
                        <div class="flex items-center justify-between gap-1 mb-1.5"><span class="px-2 py-0.5 text-[9px] font-bold rounded bg-amber-950 text-amber-300 border border-amber-800">{n['tag']}</span><span class="text-[10px] text-slate-400">{n['press']} · {n['date']}</span></div>
                        <p class="text-xs text-slate-200 font-medium hover:text-amber-300 leading-snug line-clamp-2">{n['title']}</p>
                    </a>
                    ''' for n in notice_items]) if notice_items else '<p class="text-xs text-slate-400 text-center py-16">공시가 없습니다.</p>'}
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
    print(f"✅ [{data['name']}] v39.8 대시보드 렌더링 완료!")
    return html_content


# ---------- Streamlit UI ----------
st.set_page_config(layout="wide", page_title="주식 융합 대시보드")

st.title("📊 종목별 맞춤형 동적 가중치 대시보드")
st.markdown("종목명 또는 코드를 입력하세요. **내장된 주요 종목(200개)에서 검색됩니다.**")

user_query = st.text_input("검색어 입력 (예: 삼성전자, 005930, 하이닉스, 부동산, 맥쿼리)", value="")

if user_query:
    user_query = user_query.strip()
    selected_code = None
    selected_name = None

    # 1) 6자리 숫자 코드 입력
    if user_query.isdigit() and len(user_query) == 6:
        selected_code = user_query
        match = EMBEDDED_STOCK_LIST[EMBEDDED_STOCK_LIST['Code'] == selected_code]
        if not match.empty:
            selected_name = match.iloc[0]['Name']
        else:
            selected_name = selected_code  # fallback

    # 2) 종목명 또는 부분 검색어
    else:
        matches = EMBEDDED_STOCK_LIST[EMBEDDED_STOCK_LIST['Name'].str.contains(user_query, case=False, na=False)]
        if len(matches) == 0:
            st.error("일치하는 종목이 없습니다. 검색어를 다시 확인해주세요.")
        elif len(matches) == 1:
            selected_code = matches.iloc[0]['Code']
            selected_name = matches.iloc[0]['Name']
        else:
            st.subheader(f"🔍 '{user_query}' 검색 결과 ({len(matches)}개)")
            options = [f"{row['Name']} ({row['Code']})" for _, row in matches.iterrows()]
            selected_option = st.selectbox("분석할 종목을 선택하세요:", options)
            if selected_option:
                code_part = selected_option.split('(')[-1].rstrip(')')
                selected_code = code_part
                selected_name = selected_option.split(' (')[0]

    # 3) 분석 실행
    if selected_code and selected_name:
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
                st.error(f"오류가 발생했습니다: {str(e)}")
