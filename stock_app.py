"""
내 포트폴리오 매니저 (Windows 데스크톱 앱)
여러 개의 포트폴리오에 주식 / ETF / 금 / 채권을 한국·미국 시장으로 구분해 관리하고,
지연 시세로 손익과 배당을 확인합니다.
데이터는 설치폴더\\DB\\portfolio.db 에 저장됩니다.
"""
import csv
import html
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from PySide6.QtCharts import QChart, QChartView, QPieSeries, QPieSlice
from PySide6.QtCore import QByteArray, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QMenu
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

# 1. 프로그램이 PyInstaller로 빌드된 엑셀(.exe) 실행 파일인지, 일반 파이썬 실행인지 판별
if getattr(sys, 'frozen', False):
    # .exe로 빌드된 경우: 실행 파일이 위치한 폴더 기준
    base_dir = os.path.dirname(sys.executable)
else:
    # 파이썬 스크립트로 직접 실행하는 경우: 현재 스크립트 파일이 위치한 폴더 기준
    base_dir = os.path.dirname(os.path.abspath(__file__))

# 2~3. 설치 폴더 안의 'DB' 폴더를 쓰되, 쓰기 권한이 없으면(예: Program Files) 내 계정 폴더로 대체
def _pick_app_dir():
    import tempfile

    candidates = [
        os.path.join(base_dir, "DB"),
        os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                     "PortfolioManager"),
    ]
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception:
            continue
    return tempfile.gettempdir()


APP_DIR = _pick_app_dir()

# 4. 최종 DB 파일 경로 설정 (DB/portfolio.db)
DB_PATH = os.path.join(APP_DIR, "portfolio.db")

CATS = ["주식", "ETF/ETN", "금", "채권", "현금/기타"]
MARKETS = ["한국", "미국", "기타"]
UP, DOWN = QColor("#d32f2f"), QColor("#1976d2")  # 상승 빨강 / 하락 파랑
PALETTE = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948",
           "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC", "#1F77B4", "#D62728"]
UA = {"User-Agent": "Mozilla/5.0"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
CREATE TABLE IF NOT EXISTS holdings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT, market TEXT, name TEXT, ticker TEXT, currency TEXT,
  qty REAL, avg REAL, manual_price REAL, portfolio_id INTEGER, sort INTEGER);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


# ---------------- 저장소 ----------------
class DB:
    def __init__(self):
        self.c = sqlite3.connect(DB_PATH)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        # 예전 버전 DB 이전: 포트폴리오 구분 칸을 추가하고 기존 종목을 첫 포트폴리오로 묶음
        cols = [r["name"] for r in self.q("PRAGMA table_info(holdings)")]
        if "portfolio_id" not in cols:
            try:
                shutil.copy(DB_PATH, DB_PATH + ".bak")  # 만약을 위한 백업
            except Exception:
                pass
            self.c.execute("ALTER TABLE holdings ADD COLUMN portfolio_id INTEGER")
        if "sort" not in cols:  # 사용자 지정 순서
            self.c.execute("ALTER TABLE holdings ADD COLUMN sort INTEGER")
        self.c.execute("UPDATE holdings SET sort=id WHERE sort IS NULL")
        if not self.q("SELECT id FROM portfolios"):
            self.c.execute("INSERT INTO portfolios(name) VALUES('내 포트폴리오')")
        first = self.q("SELECT id FROM portfolios ORDER BY id LIMIT 1")[0]["id"]
        self.c.execute("UPDATE holdings SET portfolio_id=? WHERE portfolio_id IS NULL", (first,))
        self.c.commit()

    def q(self, sql, args=()):
        return self.c.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        self.c.execute(sql, args)
        self.c.commit()

    def get_setting(self, key, default=""):
        r = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set_setting(self, key, value):
        self.x("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))


# ---------------- 시세 ----------------
def yahoo_quote(ticker):
    """야후 파이낸스 지연 시세 + 최근 2년 배당 기록.
    반환: (가격 또는 None, [(배당락일 'YYYY-MM-DD', 주당 배당금), ...])"""
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(ticker) + "?range=2y&interval=1d&events=div")
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
            res = json.load(r)["chart"]["result"][0]
    except Exception:
        return None, []
    price = None
    try:
        price = res["meta"].get("regularMarketPrice")
        if not price:
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            price = closes[-1]
        price = float(price)
    except Exception:
        price = None
    divs = []
    try:
        for d in (res.get("events", {}).get("dividends", {})).values():
            day = datetime.fromtimestamp(d["date"], timezone.utc).strftime("%Y-%m-%d")
            divs.append((day, float(d["amount"])))
    except Exception:
        pass
    return price, sorted(divs)


def yahoo_price(ticker):
    return yahoo_quote(ticker)[0]


def sheet_prices(url):
    """게시된 구글 시트(CSV): A열=티커, C열=가격."""
    out = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
            text = r.read().decode("utf-8-sig")
        for row in list(csv.reader(io.StringIO(text)))[1:]:
            if len(row) >= 3:
                try:
                    out[row[0].strip()] = float(row[2].replace(",", ""))
                except ValueError:
                    pass
    except Exception:
        pass
    return out


def naver_search(q):
    """한국 종목·ETF 이름 → (6자리 코드, 이름). 네이버 증권 자동완성 사용. 못 찾으면 (None, None)."""
    try:
        url = "https://ac.stock.naver.com/ac?" + urllib.parse.urlencode(
            {"q": q, "target": "stock,index,marketindicator"})
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
            data = json.load(r)
    except Exception:
        return None, None

    def strings(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, list):
            for e in x:
                yield from strings(e)
        elif isinstance(x, dict):
            for e in x.values():
                yield from strings(e)

    def rows(x):
        """6자리 코드가 정확히 하나 들어 있는 가장 작은 묶음 = 종목 한 줄."""
        if isinstance(x, (list, dict)):
            codes = [t for t in strings(x) if re.fullmatch(r"\d{6}", t)]
            if len(codes) == 1:
                yield x
            elif len(codes) > 1:
                for e in (x.values() if isinstance(x, dict) else x):
                    yield from rows(e)

    skip = {"KOSPI", "KOSDAQ", "KONEX", "KR", "stock", "STOCK", "ETF", "ETN"}
    root = data.get("items", data) if isinstance(data, dict) else data
    found = []
    for row in rows(root):
        ss = list(strings(row))
        code = next(t for t in ss if re.fullmatch(r"\d{6}", t))
        name = next((t for t in ss if t != code and t not in skip and not t.isdigit()), None)
        found.append((code, name))
    if not found:
        return None, None
    for code, name in found:  # 이름이 정확히 같은 종목 우선
        if name == q:
            return code, name
    return found[0]


def resolve_kr(q):
    """한국 종목 코드/이름 → (티커, 이름). 못 찾으면 (None, None)."""
    if q.isdigit() and len(q) <= 6:
        code, name = q.zfill(6), None
    elif "." in q:
        return q.upper(), None
    else:
        code, name = naver_search(q)
        if not code:
            return None, None
    for suf in (".KS", ".KQ"):  # 코스피 → 코스닥
        if yahoo_price(code + suf) is not None:
            return code + suf, name
    return code + ".KS", name


def normalize(market, q):
    q = q.strip()
    if not q:
        return "", None
    if market == "미국":
        return q.upper(), None
    if market == "한국":
        return resolve_kr(q)
    return q, None


class PriceWorker(QThread):
    done = Signal(dict, dict)  # (시세, 배당기록)

    def __init__(self, tickers, sheet_url):
        super().__init__()
        self.tickers, self.sheet_url = tickers, sheet_url

    def run(self):
        sp = sheet_prices(self.sheet_url) if self.sheet_url else {}
        prices, divs = {}, {}
        for t in list(self.tickers) + ["KRW=X"]:
            price, d = yahoo_quote(t)
            prices[t] = sp.get(t) or price
            divs[t] = d
        self.done.emit(prices, divs)


def _num(x):
    """'15.20배', '1,234원', 'N/A' 같은 글자에서 숫자만 뽑기."""
    if x is None:
        return None
    m = re.search(r"-?[\d,]+\.?\d*", str(x))
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def naver_fund(code6):
    """한국 종목 PER·PBR·ROE(=EPS/BPS) — 네이버 증권. 실패하면 {}."""
    try:
        url = f"https://m.stock.naver.com/api/stock/{code6}/integration"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
            d = json.load(r)
        info = {i.get("code"): i.get("value") for i in d.get("totalInfos", [])}
        per, pbr = _num(info.get("per")), _num(info.get("pbr"))
        eps, bps = _num(info.get("eps")), _num(info.get("bps"))
        roe = eps / bps * 100 if eps is not None and bps else None
        return {"per": per, "pbr": pbr, "roe": roe}
    except Exception:
        return {}


class YahooSession:
    """야후 quoteSummary는 쿠키+crumb이 필요해서 세션을 유지해 조회."""

    def __init__(self):
        import http.cookiejar

        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = list(UA.items())
        self.crumb = None

    def _get_crumb(self):
        if self.crumb:
            return True
        try:
            try:
                self.op.open("https://fc.yahoo.com", timeout=10)  # 404여도 쿠키는 받음
            except Exception:
                pass
            with self.op.open("https://query1.finance.yahoo.com/v1/test/getcrumb",
                              timeout=10) as r:
                self.crumb = r.read().decode("utf-8").strip()
        except Exception:
            self.crumb = None
        return bool(self.crumb)

    def fund(self, ticker):
        """PER·PBR·ROE. 실패하면 {}."""
        if not self._get_crumb():
            return {}
        try:
            url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                   + urllib.parse.quote(ticker)
                   + "?modules=summaryDetail,defaultKeyStatistics,financialData&crumb="
                   + urllib.parse.quote(self.crumb))
            with self.op.open(url, timeout=15) as r:
                res = json.load(r)["quoteSummary"]["result"][0]

            def raw(sec, key):
                v = (res.get(sec) or {}).get(key)
                return v.get("raw") if isinstance(v, dict) else None

            roe = raw("financialData", "returnOnEquity")
            return {"per": raw("summaryDetail", "trailingPE"),
                    "pbr": raw("defaultKeyStatistics", "priceToBook"),
                    "roe": roe * 100 if roe is not None else None}
        except Exception:
            return {}


class FundWorker(QThread):
    done = Signal(dict)

    def __init__(self, tickers):
        super().__init__()
        self.tickers = tickers

    def run(self):
        ys = YahooSession()
        out = {}
        for t in self.tickers:
            f = naver_fund(t[:6]) if t.endswith((".KS", ".KQ")) else {}
            if not any(v is not None for v in f.values()):
                f = ys.fund(t)
            out[t] = f
        self.done.emit(out)


# ---------------- 대화상자 ----------------
def spin(dec=2):
    s = QDoubleSpinBox()
    s.setDecimals(dec)
    s.setRange(0, 1e12)
    return s


class HoldingDialog(QDialog):
    """종목 추가 / 수정 공용 대화상자. h가 있으면 수정 모드."""
    HINTS = {
        "한국": "예: 005930, 삼성전자, KODEX 200",
        "미국": "예: AAPL, TLT, GLD",
        "기타": "티커가 없으면 비워 두세요",
    }

    def __init__(self, parent, portfolios, default_pid=None, h=None):
        super().__init__(parent)
        editing = h is not None
        self.setWindowTitle("종목 수정" if editing else "종목 추가")
        self.pf = QComboBox()
        for pid, pname in portfolios:
            self.pf.addItem(pname, pid)
        target = h["portfolio_id"] if editing else default_pid
        for i in range(self.pf.count()):
            if self.pf.itemData(i) == target:
                self.pf.setCurrentIndex(i)
        self.market = QComboBox()
        self.market.addItems(MARKETS)
        self.cat = QComboBox()
        self.cat.addItems(CATS)
        self.query = QLineEdit()
        self.name = QLineEdit()
        self.name.setPlaceholderText("선택 (티커 없는 자산은 필수)")
        self.qty, self.price, self.manual = spin(4), spin(4), spin(4)
        self.cur = QComboBox()
        self.cur.addItems(["KRW", "USD"])
        form = QFormLayout(self)
        form.addRow("포트폴리오", self.pf)
        form.addRow("시장", self.market)
        form.addRow("분류", self.cat)
        form.addRow("종목 (코드/이름/티커)", self.query)
        form.addRow("표시 이름", self.name)
        form.addRow("수량", self.qty)
        form.addRow("평균 매수단가" if editing else "매수 단가", self.price)
        form.addRow("통화 (기타 시장만)", self.cur)
        form.addRow("수동 현재가 (선택)", self.manual)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        self.market.currentTextChanged.connect(self.on_market)
        self.on_market(self.market.currentText())
        if editing:  # 기존 값 채우기 (시장을 먼저 → 통화는 그 다음에)
            self.market.setCurrentText(h["market"])
            self.cat.setCurrentText(h["category"])
            self.query.setText(h["ticker"] or "")
            self.name.setText(h["name"] or "")
            self.qty.setValue(h["qty"])
            self.price.setValue(h["avg"])
            self.cur.setCurrentText(h["currency"])
            self.manual.setValue(h["manual_price"] or 0)

    def on_market(self, m):
        self.query.setPlaceholderText(self.HINTS[m])
        self.cur.setEnabled(m == "기타")
        if m in ("한국", "미국"):
            self.cur.setCurrentText("KRW" if m == "한국" else "USD")

    def values(self):
        return dict(
            pf=self.pf.currentData(),
            market=self.market.currentText(), cat=self.cat.currentText(),
            query=self.query.text(), name=self.name.text().strip(),
            qty=self.qty.value(), price=self.price.value(),
            cur=self.cur.currentText(), manual=self.manual.value(),
        )


# ---------------- 메인 창 ----------------
def weights(values):
    tot = sum(v for v in values if v) or 0.0
    if tot <= 0:
        return [0.0] * len(values)
    return [(v or 0.0) / tot for v in values]


def hhi(vals):
    """허핀달지수(집중도). 1=한 종목 몰빵, 낮을수록 분산."""
    return sum(x * x for x in weights(vals))


def effective_holdings(vals):
    """유효 종목 수 = 1/HHI (분산 정도를 '실질 종목 개수'로)."""
    h = hhi(vals)
    return (1.0 / h) if h > 0 else 0.0


def top_concentration(vals, n=3):
    """상위 n개 비중 합(%)."""
    return sum(sorted(weights(vals), reverse=True)[:n]) * 100.0


def weighted_average(pairs):
    """[(가중치, 지표)] → 가중평균. 지표가 None인 종목은 제외."""
    num = den = 0.0
    for wv, m in pairs:
        if m is None or wv is None or wv <= 0:
            continue
        num += wv * m
        den += wv
    return (num / den) if den > 0 else None


def harmonic_weighted(pairs):
    """PER·PBR처럼 '가격÷이익'인 지표는 조화평균이 포트폴리오 전체 값과 맞아요."""
    den = tot = 0.0
    for wv, m in pairs:
        if m is None or wv is None or wv <= 0 or m <= 0:
            continue
        tot += wv
        den += wv / m
    return (tot / den) if den > 0 else None


def price_text(v, cur):
    """종목 자체 통화로 단가 표기 (USD는 $, KRW는 원)."""
    if v is None:
        return "-"
    return f"${v:,.2f}" if cur == "USD" else f"{v:,.0f}원"


def cell(text, right=True, color=None):
    it = QTableWidgetItem(text)
    if right:
        it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        it.setForeground(color)
    return it


class ReorderTable(QTableWidget):
    """행을 마우스로 끌어서 원하는 위치에 놓을 수 있는 표."""
    rowMoved = Signal(int, int)  # (옮길 행, 끼워 넣을 위치)

    def __init__(self, rows, cols):
        super().__init__(rows, cols)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragDropOverwriteMode(False)

    def dropEvent(self, e):
        src = self.currentRow()
        idx = self.indexAt(e.position().toPoint())
        if idx.isValid():
            below = self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem
            ins = idx.row() + (1 if below else 0)
        else:
            ins = self.rowCount()  # 표 아래 빈 곳 = 맨 끝
        e.setDropAction(Qt.IgnoreAction)  # 셀 내용이 덮어써지는 기본 동작은 막고
        e.accept()
        if src >= 0:
            self.rowMoved.emit(src, ins)  # 실제 이동은 DB 순서를 바꾸고 다시 그림


class MainWindow(QMainWindow):
    HEAD = ["포트폴리오", "분류", "시장", "이름", "티커", "수량", "평균단가", "현재가",
            "투자금(원)", "평가금액(원)", "손익(원)", "수익률(%)",
            "통화", "비중(%)", "배당수익률(%)", "PER", "PBR", "ROE(%)"]
    NAME_COL = 3  # 이름 열은 숨길 수 없게 고정
    COL_DEFAULT_HIDDEN = (12, 13, 14, 15, 16, 17)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("내 포트폴리오 매니저")
        self.resize(1300, 780)
        self.db = DB()
        self.prices, self.worker, self.row_ids = {}, None, []
        self.fund_worker = None
        try:
            self.fund = json.loads(self.db.get_setting("fund_cache", "{}"))
        except Exception:
            self.fund = {}

        root = QWidget()
        lay = QVBoxLayout(root)
        pbar = QHBoxLayout()
        pbar.addWidget(QLabel("포트폴리오"))
        self.pf = QComboBox()
        self.pf.setMinimumWidth(200)
        pbar.addWidget(self.pf)
        for text, fn in [("새 포트폴리오", self.on_pf_new), ("이름 변경", self.on_pf_rename),
                         ("포트폴리오 삭제", self.on_pf_delete)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            pbar.addWidget(b)
        saved = self.db.get_setting("pf_id", "")
        first_id = self.db.q("SELECT id FROM portfolios ORDER BY id LIMIT 1")[0]["id"]
        try:
            sel = first_id if saved == "" else (None if saved == "all" else int(saved))
        except ValueError:
            sel = first_id
        self.load_portfolios(sel)
        self.pf.currentIndexChanged.connect(self.on_pf_changed)

        bar = pbar  # 포트폴리오 선택 줄 오른쪽에 표시 통화 · 시트 · 새로고침을 이어 붙임
        bar.addStretch()
        self.fx_label = QLabel("USD/KRW -")
        self.fx_label.setStyleSheet("font-weight:600; color:#1565c0; padding-right:12px;")
        self.fx_label.setToolTip("달러/원 환율 (새로고침할 때 갱신되는 지연 시세)")
        bar.addWidget(self.fx_label)
        bar.addWidget(QLabel("표시 통화"))
        self.cur_mode = QComboBox()
        self.cur_mode.addItems(["원 (KRW)", "달러 (USD)"])
        self.cur_mode.setCurrentIndex(1 if self.db.get_setting("disp_cur") == "USD" else 0)
        self.cur_mode.currentIndexChanged.connect(self.on_cur_mode)
        bar.addWidget(self.cur_mode)
        bar.addWidget(QLabel("구글 시트 CSV"))
        self.sheet = QLineEdit(self.db.get_setting("sheet_url"))
        self.sheet.setPlaceholderText("선택: 게시된 CSV 주소")
        self.sheet.setMinimumWidth(200)
        self.sheet.editingFinished.connect(self.on_sheet)
        bar.addWidget(self.sheet)
        rb = QPushButton("⟳ 새로고침")
        rb.clicked.connect(self.refresh)
        bar.addWidget(rb)
        lay.addLayout(bar)

        srow = QHBoxLayout()
        self.sums = {}
        for key in ["총 평가금액", "총 투자금", "평가 손익"]:
            lb = QLabel()
            lb.setStyleSheet("font-size:16px; padding:6px;")
            srow.addWidget(lb)
            self.sums[key] = lb
        lay.addLayout(srow)

        self.table = ReorderTable(0, len(self.HEAD))
        self.table.setHorizontalHeaderLabels(self.HEAD)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(lambda *_: self.on_edit())  # 더블클릭 = 수정
        self.init_columns()
        self.build_col_menu()
        self.table.rowMoved.connect(
            lambda src, ins: QTimer.singleShot(0, lambda: self.reorder(src, ins)))
        self.chartview = QChartView()
        self.chartview.setRenderHint(QPainter.Antialiasing)
        self.chart_data = ({}, {}, 0.0)
        self.chart_mode = QComboBox()
        self.chart_mode.addItems(["자산군 비중", "종목별 비중"])
        self.chart_mode.currentIndexChanged.connect(self.draw_chart)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.chart_mode)
        pl.addWidget(self.chartview)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        hb = QHBoxLayout()
        for text, fn in [("종목 추가", self.on_buy), ("종목 수정", self.on_edit),
                         ("종목 삭제", self.on_delete), ("분류 변경", self.on_category)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            hb.addWidget(b)
        for text, fn in [("▲ 위로", lambda: self.move_selected(-1)),
                         ("▼ 아래로", lambda: self.move_selected(1))]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            b.setToolTip("표에서 행을 마우스로 끌어서 옮길 수도 있어요")
            hb.addWidget(b)
        sortbtn = QPushButton("정렬")
        sortmenu = QMenu(self)
        sortmenu.addAction("사용자 순서로 보기", self.clear_sort)
        sortmenu.addAction("현재 정렬을 사용자 순서로 저장", self.fix_sort)
        sortmenu.addSeparator()
        sortmenu.addAction("평가금액 큰 순", lambda: self.set_sort(9, True))
        sortmenu.addAction("수익률 높은 순", lambda: self.set_sort(11, True))
        sortmenu.addAction("이름순", lambda: self.set_sort(3, False))
        sortbtn.setMenu(sortmenu)
        hb.addWidget(sortbtn)
        hb.addStretch()
        colbtn = QPushButton("열 설정")
        colbtn.setMenu(self.col_menu)
        hb.addWidget(colbtn)
        ll.addLayout(hb)
        ll.addWidget(self.table)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(panel)
        split.setSizes([760, 540])

        self.divs = {}
        divw = QWidget()
        dl = QVBoxLayout(divw)
        self.div_note = QLabel(
            "과거 12개월의 배당 기록을 1년 뒤로 옮긴 '예상치'예요. 금액은 세전이고, "
            "실제 지급일은 배당락일보다 보통 수 주~수 개월 뒤예요.")
        self.div_note.setWordWrap(True)
        self.div_note.setStyleSheet("color:#666; padding:4px;")
        self.div_total = QLabel("배당 정보를 불러오는 중…")
        self.div_total.setStyleSheet("font-size:16px; padding:4px;")
        self.mtable = QTableWidget(0, 3)
        self.mtable.setHorizontalHeaderLabels(["월 (배당락일 기준)", "예상 배당금(원)", "배당 주는 종목"])
        self.dtable = QTableWidget(0, 6)
        self.dtable.setHorizontalHeaderLabels(
            ["예상 배당락일", "종목", "주당 배당금", "수량", "예상 수령액(원)", "근거 (작년 배당락일)"])
        for t in (self.mtable, self.dtable):
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.divchart = QChartView()
        self.divchart.setRenderHint(QPainter.Antialiasing)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.mtable)
        top.addWidget(self.divchart)
        top.setSizes([650, 600])
        dsplit = QSplitter(Qt.Vertical)
        dsplit.addWidget(top)
        dsplit.addWidget(self.dtable)
        dsplit.setSizes([360, 300])
        dl.addWidget(self.div_note)
        dl.addWidget(self.div_total)
        dl.addWidget(dsplit)

        # ----- 분석 탭 (배분·집중도 / 밸류에이션) -----
        alloc = QWidget()
        av = QVBoxLayout(alloc)
        prow = QHBoxLayout()
        self.pie_cat, self.pie_mkt, self.pie_cur = QChartView(), QChartView(), QChartView()
        for v in (self.pie_cat, self.pie_mkt, self.pie_cur):
            v.setRenderHint(QPainter.Antialiasing)
            v.setMinimumHeight(300)
            prow.addWidget(v)
        av.addLayout(prow)
        self.conc_label = QLabel()
        self.conc_label.setTextFormat(Qt.RichText)
        self.conc_label.setWordWrap(True)
        self.conc_label.setStyleSheet(
            "background:#f6f8fa; border:1px solid #d0d7de; border-radius:8px; padding:10px;")
        av.addWidget(self.conc_label)
        av.addStretch()

        valw = QWidget()
        vv = QVBoxLayout(valw)
        vbar = QHBoxLayout()
        self.fund_btn = QPushButton("📥 PER·PBR·ROE 불러오기")
        self.fund_btn.clicked.connect(self.on_fund)
        self.an_status = QLabel("")
        self.an_status.setStyleSheet("color:#666;")
        vbar.addWidget(self.fund_btn)
        vbar.addWidget(self.an_status)
        vbar.addStretch()
        vv.addLayout(vbar)
        self.val_label = QLabel()
        self.val_label.setTextFormat(Qt.RichText)
        self.val_label.setWordWrap(True)
        self.val_label.setStyleSheet(
            "background:#f6f8fa; border:1px solid #d0d7de; border-radius:8px; padding:10px;"
            " font-size:15px;")
        vv.addWidget(self.val_label)
        self.vtable = QTableWidget(0, 9)
        self.vtable.setEditTriggers(QTableWidget.NoEditTriggers)
        self.vtable.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        vv.addWidget(self.vtable, 1)
        vnote = QLabel("참고용 분석이며 투자 권유가 아니에요. PER·PBR은 평가금액 비중으로 합산한 값이고, "
                       "적자 종목·ETF·금처럼 값이 없는 자산은 제외돼요. 배당수익률은 최근 12개월 "
                       "배당금 ÷ 현재가로 계산해요.")
        vnote.setWordWrap(True)
        vnote.setStyleSheet("color:#666; font-size:11px;")
        vv.addWidget(vnote)

        sub = QTabWidget()
        sub.addTab(alloc, "배분·집중도")
        sub.addTab(valw, "밸류에이션")

        tabs = QTabWidget()
        tabs.addTab(split, "포트폴리오")
        tabs.addTab(sub, "분석")
        tabs.addTab(divw, "배당")
        lay.addWidget(tabs)
        self.setCentralWidget(root)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5 * 60 * 1000)
        self.render()
        self.refresh()

    # ----- 시세 -----
    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        tickers = sorted({r["ticker"] for r in self.db.q("SELECT ticker FROM holdings")
                          if r["ticker"]})
        self.statusBar().showMessage("시세 불러오는 중…")
        self.worker = PriceWorker(tickers, self.sheet.text().strip())
        self.worker.done.connect(self.on_prices)
        self.worker.start()

    def on_prices(self, prices, divs):
        self.prices = prices
        self.divs = divs
        self.render()
        if self.need_autosize and self.table.rowCount():
            self.autosize_cols()
            self.need_autosize = False
        self.statusBar().showMessage(
            f"지연 시세 · 마지막 갱신 {datetime.now():%H:%M:%S} · 5분마다 자동 갱신")

    # ----- 정렬 / 순서 이동 -----
    NUMERIC_COLS = (5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17)

    def calc(self, h, fx):
        """정렬용 계산값 (render와 같은 방식)."""
        if h["manual_price"] and h["manual_price"] > 0:
            price = h["manual_price"]
        else:
            price = self.prices.get(h["ticker"]) if h["ticker"] else None
        rate = fx if h["currency"] == "USD" else 1.0
        cost = h["qty"] * h["avg"] * rate
        value = h["qty"] * price * rate if price else cost
        fd = (self.fund.get(h["ticker"]) or {}) if h["ticker"] else {}
        ds = self.divs.get(h["ticker"]) if h["ticker"] else None
        yld = None
        if ds is not None and price:
            cutoff = (date.today() - timedelta(days=365)).isoformat()
            yld = sum(a for d, a in ds if d >= cutoff) / price * 100
        return dict(price=price, rate=rate, cost=cost, value=value, pl=value - cost,
                    ret=(value / cost - 1) * 100 if cost else 0.0, yld=yld,
                    per=fd.get("per"), pbr=fd.get("pbr"), roe=fd.get("roe"))

    def sorted_holdings(self, holdings, fx):
        col = self.sort_col
        if col is None:
            return holdings  # 사용자 지정 순서 그대로

        def val(h, c):
            return {
                0: self.pf_names.get(h["portfolio_id"], ""), 1: h["category"] or "",
                2: h["market"] or "", 3: (h["name"] or "").lower(), 4: h["ticker"] or "",
                5: h["qty"], 6: h["avg"] * c["rate"],
                7: c["price"] * c["rate"] if c["price"] else None,
                8: c["cost"], 9: c["value"], 10: c["pl"], 11: c["ret"],
                12: h["currency"] or "", 13: c["value"], 14: c["yld"],
                15: c["per"], 16: c["pbr"], 17: c["roe"],
            }[col]

        keyed = [(val(h, self.calc(h, fx)), h) for h in holdings]
        real = sorted([t for t in keyed if t[0] is not None],
                      key=lambda t: t[0], reverse=self.sort_desc)
        return [h for _, h in real] + [h for v, h in keyed if v is None]

    def update_sort_indicator(self):
        h = self.table.horizontalHeader()
        if self.sort_col is None:
            h.setSortIndicatorShown(False)
        else:
            h.setSortIndicatorShown(True)
            h.setSortIndicator(
                self.sort_col, Qt.DescendingOrder if self.sort_desc else Qt.AscendingOrder)

    def on_header_click(self, col):
        first = col in self.NUMERIC_COLS  # 숫자 열은 큰 값부터(내림차순), 글자 열은 가나다순부터
        if self.sort_col == col:
            if self.sort_desc == first:  # 첫 방향이었다면 반대 방향으로
                self.sort_desc = not first
            else:
                self.sort_col = None  # 세 번째 클릭 = 사용자 순서로 복귀
        else:
            self.sort_col = col
            self.sort_desc = first
        self.update_sort_indicator()
        self.render()
        self.statusBar().showMessage(
            "열 제목을 누르면 정렬돼요 (같은 열을 세 번 누르면 사용자 순서로 돌아가요).", 5000)

    def set_sort(self, col, desc):
        self.sort_col, self.sort_desc = col, desc
        self.update_sort_indicator()
        self.render()

    def clear_sort(self):
        self.sort_col = None
        self.update_sort_indicator()
        self.render()

    def fix_sort(self):
        """지금 보이는 정렬 순서를 사용자 순서로 저장."""
        if self.sort_col is None:
            self.statusBar().showMessage("이미 사용자 순서로 보고 있어요.", 4000)
            return
        self.apply_order(list(self.row_ids))
        self.clear_sort()
        self.statusBar().showMessage("정렬된 순서를 사용자 순서로 저장했어요.", 4000)

    def apply_order(self, ids):
        """ids(화면 순서)대로 저장. 다른 포트폴리오 행의 상대 순서는 그대로 둠."""
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        slots = sorted(r["sort"] for r in
                       self.db.q(f"SELECT sort FROM holdings WHERE id IN ({marks})", ids))
        for hid, slot in zip(ids, slots):
            self.db.c.execute("UPDATE holdings SET sort=? WHERE id=?", (slot, hid))
        self.db.c.commit()

    def reorder(self, src, ins):
        """src번 행을 ins 위치(원래 목록 기준 끼워 넣을 자리)로 옮김."""
        ids = list(self.row_ids)
        if not (0 <= src < len(ids)):
            return
        if self.sort_col is not None:  # 정렬 중이면 지금 순서를 먼저 사용자 순서로 고정
            self.apply_order(ids)
            self.sort_col = None
            self.update_sort_indicator()
            self.statusBar().showMessage("정렬된 순서를 사용자 순서로 저장했어요.", 4000)
        item = ids.pop(src)
        if ins > src:
            ins -= 1
        ins = max(0, min(ins, len(ids)))
        ids.insert(ins, item)
        self.apply_order(ids)
        self.render()
        self.table.selectRow(ins)

    def move_selected(self, delta):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.row_ids):
            QMessageBox.information(self, "안내", "표에서 옮길 종목을 먼저 선택해 주세요.")
            return
        new = r + delta
        if not (0 <= new < len(self.row_ids)):
            return
        self.reorder(r, new + 1 if delta > 0 else new)

    # ----- 표 열 설정 -----
    def init_columns(self):
        h = self.table.horizontalHeader()
        self.sort_col, self.sort_desc = None, False  # None = 사용자 지정 순서
        h.setSectionsClickable(True)
        h.setSortIndicatorShown(False)
        h.sectionClicked.connect(self.on_header_click)
        h.setSectionResizeMode(QHeaderView.Interactive)  # 열 경계를 끌어서 너비 조절
        h.setStretchLastSection(False)
        h.setSectionsMovable(True)  # 열 머리글을 끌어서 순서 이동
        h.setMinimumSectionSize(40)
        self.col_save = QTimer(self)
        self.col_save.setSingleShot(True)
        self.col_save.setInterval(600)
        self.col_save.timeout.connect(self.save_cols)
        self.need_autosize = True
        restored = False
        saved = self.db.get_setting("col_state", "")
        if saved:
            try:
                restored = bool(h.restoreState(QByteArray.fromHex(saved.encode("ascii"))))
            except Exception:
                restored = False
        if restored:
            self.need_autosize = False
        else:
            for i in self.COL_DEFAULT_HIDDEN:
                self.table.setColumnHidden(i, True)
        h.sectionResized.connect(lambda *_: self.col_save.start())
        h.sectionMoved.connect(lambda *_: self.col_save.start())

    def build_col_menu(self):
        self.col_menu = QMenu(self)
        self.col_actions = []
        for i, t in enumerate(self.HEAD):
            act = self.col_menu.addAction(t.replace("(원)", ""))
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(i))
            act.toggled.connect(lambda checked, i=i: self.set_col_visible(i, checked))
            self.col_actions.append(act)
        self.col_menu.addSeparator()
        self.col_menu.addAction("열 너비 자동 맞춤", self.autosize_cols)
        self.col_menu.addAction("기본값으로 되돌리기", self.reset_cols)

    def set_col_visible(self, i, visible):
        if i == self.NAME_COL and not visible:  # 이름 열은 항상 보이게
            self.col_actions[i].blockSignals(True)
            self.col_actions[i].setChecked(True)
            self.col_actions[i].blockSignals(False)
            return
        self.table.setColumnHidden(i, not visible)
        if visible and self.table.columnWidth(i) < 60:
            self.table.setColumnWidth(i, 90)
        self.save_cols()

    def autosize_cols(self):
        self.table.resizeColumnsToContents()
        for i in range(self.table.columnCount()):
            if not self.table.isColumnHidden(i):
                self.table.setColumnWidth(i, self.table.columnWidth(i) + 14)
        self.save_cols()

    def reset_cols(self):
        h = self.table.horizontalHeader()
        for v in range(h.count()):  # 열 순서 원래대로
            h.moveSection(h.visualIndex(v), v)
        for i in range(self.table.columnCount()):
            hide = i in self.COL_DEFAULT_HIDDEN
            self.table.setColumnHidden(i, hide)
            self.col_actions[i].blockSignals(True)
            self.col_actions[i].setChecked(not hide)
            self.col_actions[i].blockSignals(False)
        self.autosize_cols()

    def save_cols(self):
        try:
            state = self.table.horizontalHeader().saveState().toHex().data().decode("ascii")
            self.db.set_setting("col_state", state)
        except Exception:
            pass

    def usd(self):
        return self.cur_mode.currentIndex() == 1

    def num(self, krw, fx):
        """표 안의 숫자 (단위는 머리글에 표시)."""
        return f"{krw / fx:,.2f}" if self.usd() else f"{krw:,.0f}"

    def money(self, krw, fx):
        """요약 문구용 (기호 포함)."""
        if self.usd():
            v = krw / fx
            return f"{'-' if v < 0 else ''}${abs(v):,.2f}"
        return f"{krw:,.0f}원"

    def on_cur_mode(self):
        self.db.set_setting("disp_cur", "USD" if self.usd() else "KRW")
        self.render()

    def on_sheet(self):
        self.db.set_setting("sheet_url", self.sheet.text().strip())
        self.refresh()

    # ----- 화면 -----
    def render(self):
        fx_real = self.prices.get("KRW=X")
        fx = fx_real or 1400.0
        self.fx_label.setText(
            f"USD/KRW {fx:,.2f}" if fx_real else f"USD/KRW {fx:,.0f} (임시값)")
        holdings = self.sorted_holdings(self.holdings(), fx)
        self.row_ids = [h["id"] for h in holdings]
        self.table.setRowCount(len(holdings))
        unit = "USD" if self.usd() else "원"
        self.table.setHorizontalHeaderLabels([t.replace("(원)", f"({unit})") for t in self.HEAD])
        tv = tc = 0.0
        by_cat, by_name = {}, {}
        by_mkt, by_cur, rows_calc = {}, {}, []
        values_list = []
        cutoff = (date.today() - timedelta(days=365)).isoformat()

        def f1(v):
            return f"{v:,.1f}" if v is not None else "-"
        for i, h in enumerate(holdings):
            if h["manual_price"] and h["manual_price"] > 0:
                price = h["manual_price"]
            else:
                price = self.prices.get(h["ticker"]) if h["ticker"] else None
            rate = fx if h["currency"] == "USD" else 1.0
            cost = h["qty"] * h["avg"] * rate
            value = h["qty"] * price * rate if price else cost
            pl = value - cost
            ret = (value / cost - 1) * 100 if cost else 0.0
            tv += value
            tc += cost
            by_cat[h["category"]] = by_cat.get(h["category"], 0) + value
            by_name[h["name"]] = by_name.get(h["name"], 0) + value
            by_mkt[h["market"]] = by_mkt.get(h["market"], 0) + value
            by_cur[h["currency"]] = by_cur.get(h["currency"], 0) + value
            rows_calc.append(dict(name=h["name"], ticker=h["ticker"] or "", price=price,
                                  value=value, cost=cost, pl=pl))
            values_list.append(value)
            fd = (self.fund.get(h["ticker"]) or {}) if h["ticker"] else {}
            ds = self.divs.get(h["ticker"]) if h["ticker"] else None
            ylds = None
            if ds is not None and price:
                ylds = sum(a for d, a in ds if d >= cutoff) / price * 100
            color = UP if pl > 0 else DOWN if pl < 0 else None
            vals = [
                cell(self.pf_names.get(h["portfolio_id"], "-"), False),
                cell(h["category"], False), cell(h["market"], False),
                cell(h["name"], False), cell(h["ticker"] or "-", False),
                cell(f"{h['qty']:,.4g}"), cell(price_text(h["avg"], h["currency"])),
                cell(price_text(price, h["currency"])),
                cell(self.num(cost, fx)), cell(self.num(value, fx)),
                cell(self.num(pl, fx), color=color), cell(f"{ret:.2f}", color=color),
                cell(h["currency"], False), cell("-"),  # 비중은 합계를 알고 난 뒤 채움
                cell(f"{ylds:.2f}" if ylds is not None else "-"),
                cell(f1(fd.get("per"))), cell(f1(fd.get("pbr"))), cell(f1(fd.get("roe"))),
            ]
            for j, it in enumerate(vals):
                self.table.setItem(i, j, it)

        for i, v in enumerate(values_list):  # 비중(%) 열
            self.table.setItem(i, 13, cell(f"{v / tv * 100:.1f}" if tv else "-"))

        pl_total = tv - tc
        pct = pl_total / tc * 100 if tc else 0.0
        
        self.sums["총 평가금액"].setText(f"총 평가금액\n{self.money(tv, fx)}")
        self.sums["총 투자금"].setText(f"총 투자금\n{self.money(tc, fx)}")

        # [핵심 1] QLabel이 HTML(Rich Text) 태그를 인식하도록 설정
        self.sums["평가 손익"].setTextFormat(Qt.RichText)

        # [핵심 2] 부호에 따라 적용할 색상 지정
        if pl_total > 0:
            color_style = "color: red;"
        elif pl_total < 0:
            color_style = "color: blue;"
        else:
            color_style = "color: black;"

        # [핵심 3] 제목은 검은색, 숫자 및 퍼센트만 <span> 태그로 색상 적용
        value_text = f"{self.money(pl_total, fx)} ({pct:+.2f}%)"
        self.sums["평가 손익"].setText(f"평가 손익<br><span style='{color_style}'>{value_text}</span>")
        
        self.chart_data = (by_cat, by_name, tv)
        self.draw_chart()
        self.render_dividends(holdings, fx)
        try:  # 분석 탭에 문제가 생겨도 메인 화면은 계속 동작하게
            self.render_analysis(rows_calc, by_cat, by_mkt, by_cur, by_name, tv, tc, fx)
        except Exception as ex:
            self.an_status.setText(f"분석 표시 오류: {ex}")

    def make_pie(self, data, title):
        items = [(k, v) for k, v in sorted(data.items(), key=lambda kv: -kv[1]) if v > 0]
        total = sum(v for _, v in items)
        series = QPieSeries()
        series.setHoleSize(0.35)
        for n, (k, v) in enumerate(items):
            pct = v / total * 100
            short = k if len(k) <= 14 else k[:13] + "…"
            sl = series.append(f"{short}", v)
            sl.setBrush(QColor(PALETTE[n % len(PALETTE)]))  # 조각마다 다른 색
            sl.setLabelVisible(pct >= 2)  # 너무 작은 조각은 범례로만 표시
            sl.setLabelPosition(QPieSlice.LabelOutside)
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(title)
        chart.legend().setAlignment(Qt.AlignBottom)
        for marker, (k, v) in zip(chart.legend().markers(), items):
            short = k if len(k) <= 14 else k[:13] + "…"
            marker.setLabel(f"{short} {v / total * 100:.1f}%")
        return chart

    def draw_chart(self):
        by_cat, by_name, tv = self.chart_data
        data = by_cat if self.chart_mode.currentIndex() == 0 else by_name
        self.chartview.setChart(self.make_pie(data, self.chart_mode.currentText()))

    def render_analysis(self, rows, by_cat, by_mkt, by_cur, by_name, tv, tc, fx):
        # ----- 배분·집중도 -----
        self.pie_cat.setChart(self.make_pie(by_cat, "자산군"))
        self.pie_mkt.setChart(self.make_pie(by_mkt, "시장"))
        self.pie_cur.setChart(self.make_pie(by_cur, "통화"))
        vals = list(by_name.values())
        if not vals or tv <= 0:
            self.conc_label.setText("보유 종목을 추가하면 집중도가 계산돼요.")
        else:
            hh, eff, top3 = hhi(vals), effective_holdings(vals), top_concentration(vals, 3)
            top_name, top_v = max(by_name.items(), key=lambda kv: kv[1])
            if hh < 0.15:
                msg = "분산이 잘 되어 있는 편이에요."
            elif hh < 0.25:
                msg = "보통 수준의 집중도예요."
            else:
                msg = "소수 종목에 많이 몰려 있어요."
            self.conc_label.setText(
                f"<b>종목 수</b> {len(vals)}개 &nbsp;·&nbsp; <b>HHI</b> {hh:.3f} &nbsp;·&nbsp; "
                f"<b>유효 종목 수</b> {eff:.1f}개 &nbsp;·&nbsp; <b>상위 3개 비중</b> {top3:.1f}% "
                f"&nbsp;·&nbsp; <b>최대 종목</b> {html.escape(top_name)} {top_v / tv * 100:.1f}%"
                f"<br>→ {msg} <span style='color:#666'>(HHI는 1에 가까울수록 한 종목에 "
                f"몰려 있다는 뜻이에요. 0.15 미만 분산 / 0.25 이상 집중은 일반적인 참고 기준이에요.)</span>")

        # ----- 밸류에이션 -----
        unit = "USD" if self.usd() else "원"
        self.vtable.setHorizontalHeaderLabels(
            ["종목", f"평가금액({unit})", "비중(%)", "PER", "PBR", "ROE(%)",
             "배당수익률(%)", f"손익({unit})", "손익 기여(%p)"])
        cutoff = (date.today() - timedelta(days=365)).isoformat()
        ent = []
        for r in rows:
            f = (self.fund.get(r["ticker"]) or {}) if r["ticker"] else {}
            ds = self.divs.get(r["ticker"]) if r["ticker"] else None
            yld = None
            if ds is not None and r["price"]:
                yld = sum(a for d, a in ds if d >= cutoff) / r["price"] * 100
            ent.append(dict(r, per=f.get("per"), pbr=f.get("pbr"), roe=f.get("roe"), yld=yld))
        ent.sort(key=lambda e: -e["value"])

        per = harmonic_weighted([(e["value"], e["per"]) for e in ent])
        pbr = harmonic_weighted([(e["value"], e["pbr"]) for e in ent])
        roe = weighted_average([(e["value"], e["roe"]) for e in ent])
        yld = weighted_average([(e["value"], e["yld"]) for e in ent])

        def f1(v, suf):
            return f"{v:,.1f}{suf}" if v is not None else "-"

        cov = sum(e["value"] for e in ent
                  if e["per"] is not None or e["pbr"] is not None or e["roe"] is not None)
        if not ent:
            self.val_label.setText("보유 종목을 추가하면 밸류에이션이 계산돼요.")
        else:
            tail = ""
            if self.fund and tv > 0:
                tm = self.db.get_setting("fund_time", "")
                tail = (f"<br><span style='color:#666; font-size:12px'>PER·PBR·ROE 정보가 있는 종목이 "
                        f"평가금액의 {cov / tv * 100:.0f}%예요 · 마지막 불러온 시각 {tm}</span>")
            elif not self.fund:
                tail = "<br><span style='color:#666'>위의 ‘PER·PBR·ROE 불러오기’를 눌러 주세요.</span>"
            self.val_label.setText(
                f"<b>PER</b> {f1(per, '배')} &nbsp;·&nbsp; <b>PBR</b> {f1(pbr, '배')} "
                f"&nbsp;·&nbsp; <b>ROE</b> {f1(roe, '%')} &nbsp;·&nbsp; "
                f"<b>배당수익률</b> {f1(yld, '%')}{tail}")

        self.vtable.setRowCount(len(ent))
        for i, e in enumerate(ent):
            color = UP if e["pl"] > 0 else DOWN if e["pl"] < 0 else None
            share = e["value"] / tv * 100 if tv else 0.0
            contrib = e["pl"] / tc * 100 if tc else 0.0
            row = [cell(e["name"], False), cell(self.num(e["value"], fx)), cell(f"{share:.1f}"),
                   cell(f1(e["per"], "")), cell(f1(e["pbr"], "")), cell(f1(e["roe"], "")),
                   cell(f1(e["yld"], "")), cell(self.num(e["pl"], fx), color=color),
                   cell(f"{contrib:+.2f}", color=color)]
            for j, it in enumerate(row):
                self.vtable.setItem(i, j, it)
        if self.fund:
            self.an_status.setText("")

    def on_fund(self):
        if self.fund_worker and self.fund_worker.isRunning():
            return
        tickers = sorted({h["ticker"] for h in self.holdings() if h["ticker"]})
        if not tickers:
            self.an_status.setText("티커가 있는 종목을 먼저 추가해 주세요.")
            return
        self.fund_btn.setEnabled(False)
        self.an_status.setText("불러오는 중… (종목당 1~2초 걸려요)")
        self.fund_worker = FundWorker(tickers)
        self.fund_worker.done.connect(self.on_fund_done)
        self.fund_worker.start()

    def on_fund_done(self, out):
        self.fund.update(out)
        self.db.set_setting("fund_cache", json.dumps(self.fund))
        self.db.set_setting("fund_time", datetime.now().strftime("%Y-%m-%d %H:%M"))
        self.fund_btn.setEnabled(True)
        got = sum(1 for f in out.values() if any(v is not None for v in f.values()))
        self.render()
        self.an_status.setText(f"{len(out)}개 중 {got}개 종목의 정보를 받았어요.")

    def render_dividends(self, holdings, fx):
        today = date.today()
        unit = "USD" if self.usd() else "원"
        self.mtable.setHorizontalHeaderLabels(
            ["월 (배당락일 기준)", f"예상 배당금({unit})", "배당 주는 종목"])
        self.dtable.setHorizontalHeaderLabels(
            ["예상 배당락일", "종목", "주당 배당금", "수량", f"예상 수령액({unit})",
             "근거 (작년 배당락일)"])
        months = []
        y, m = today.year, today.month
        for _ in range(12):
            months.append((y, m))
            m += 1
            if m == 13:
                y, m = y + 1, 1

        items = []  # (예상 배당락일, 보유종목, 주당배당금, 작년 배당락일, 예상수령액(원))
        for h in holdings:
            if not h["ticker"]:
                continue
            rate = fx if h["currency"] == "USD" else 1.0
            for ds, amt in self.divs.get(h["ticker"], []):
                d0 = date.fromisoformat(ds)
                try:
                    nxt = d0.replace(year=d0.year + 1)
                except ValueError:  # 2월 29일
                    nxt = d0.replace(year=d0.year + 1, day=28)
                if nxt >= today and (nxt.year, nxt.month) in months:
                    items.append((nxt, h, amt, d0, amt * h["qty"] * rate))
        items.sort(key=lambda x: x[0])

        agg = {k: [0.0, []] for k in months}
        for nxt, h, amt, d0, krw in items:
            k = (nxt.year, nxt.month)
            agg[k][0] += krw
            if h["name"] not in agg[k][1]:
                agg[k][1].append(h["name"])

        by_div = {}
        for nxt, h, amt, d0, krw in items:
            by_div[h["name"]] = by_div.get(h["name"], 0) + krw
        self.divchart.setChart(self.make_pie(by_div, "종목별 배당 비중 (향후 12개월 예상)"))

        self.mtable.setRowCount(len(months))
        for i, k in enumerate(months):
            tot, names = agg[k]
            self.mtable.setItem(i, 0, cell(f"{k[0]}년 {k[1]}월", False))
            self.mtable.setItem(i, 1, cell(self.num(tot, fx) if tot else "-"))
            self.mtable.setItem(i, 2, cell(", ".join(names) or "-", False))

        self.dtable.setRowCount(len(items))
        for i, (nxt, h, amt, d0, krw) in enumerate(items):
            per = f"{amt:,.2f} USD" if h["currency"] == "USD" else f"{amt:,.0f} KRW"
            row = [cell(nxt.strftime("%Y-%m-%d"), False), cell(h["name"], False),
                   cell(per), cell(f"{h['qty']:,.4g}"), cell(self.num(krw, fx)),
                   cell(d0.strftime("%Y-%m-%d"), False)]
            for j, it in enumerate(row):
                self.dtable.setItem(i, j, it)

        year_total = sum(v[0] for v in agg.values())
        if not holdings:
            self.div_total.setText("보유 종목을 추가하면 배당 일정이 나타나요.")
        elif not self.divs:
            self.div_total.setText("배당 정보를 불러오는 중…")
        elif not items:
            self.div_total.setText("향후 12개월 예상 배당이 없어요 (무배당 종목이거나 데이터가 없어요).")
        else:
            self.div_total.setText(
                f"향후 12개월 예상 배당금(세전): {self.money(year_total, fx)}"
                f" · 월 평균 {self.money(year_total / 12, fx)}")

    # ----- 포트폴리오 -----
    def pid(self):
        """현재 선택된 포트폴리오 id (전체 합산이면 None)."""
        return self.pf.currentData()

    def holdings(self):
        if self.pid() is None:
            return self.db.q("SELECT * FROM holdings ORDER BY sort, id")
        return self.db.q(
            "SELECT * FROM holdings WHERE portfolio_id=? ORDER BY sort, id", (self.pid(),))

    def load_portfolios(self, select=None):
        rows = self.db.q("SELECT id, name FROM portfolios ORDER BY id")
        self.pf_names = {r["id"]: r["name"] for r in rows}
        self.pf.blockSignals(True)
        self.pf.clear()
        self.pf.addItem("전체 합산", None)
        idx = 0
        for r in rows:
            self.pf.addItem(r["name"], r["id"])
            if select is not None and r["id"] == select:
                idx = self.pf.count() - 1
        self.pf.setCurrentIndex(idx)
        self.pf.blockSignals(False)

    def on_pf_changed(self):
        self.db.set_setting("pf_id", "all" if self.pid() is None else str(self.pid()))
        self.render()

    def on_pf_new(self):
        name, ok = QInputDialog.getText(
            self, "새 포트폴리오", "포트폴리오 이름", QLineEdit.Normal, "")
        name = name.strip()
        if not ok or not name:
            return
        if name in self.pf_names.values():
            QMessageBox.warning(self, "확인", "같은 이름의 포트폴리오가 이미 있어요.")
            return
        self.db.x("INSERT INTO portfolios(name) VALUES(?)", (name,))
        new_id = self.db.q("SELECT MAX(id) AS m FROM portfolios")[0]["m"]
        self.load_portfolios(new_id)
        self.on_pf_changed()

    def on_pf_rename(self):
        pid = self.pid()
        if pid is None:
            QMessageBox.information(self, "안내", "이름을 바꿀 포트폴리오를 먼저 선택해 주세요.")
            return
        name, ok = QInputDialog.getText(
            self, "이름 변경", "새 이름", QLineEdit.Normal, self.pf_names.get(pid, ""))
        name = name.strip()
        if not ok or not name or name == self.pf_names.get(pid):
            return
        if name in self.pf_names.values():
            QMessageBox.warning(self, "확인", "같은 이름의 포트폴리오가 이미 있어요.")
            return
        self.db.x("UPDATE portfolios SET name=? WHERE id=?", (name, pid))
        self.load_portfolios(pid)
        self.on_pf_changed()

    def on_pf_delete(self):
        pid = self.pid()
        if pid is None:
            QMessageBox.information(self, "안내", "삭제할 포트폴리오를 먼저 선택해 주세요.")
            return
        if len(self.pf_names) <= 1:
            QMessageBox.warning(self, "확인", "마지막 하나 남은 포트폴리오는 삭제할 수 없어요.")
            return
        n = self.db.q("SELECT COUNT(*) AS c FROM holdings WHERE portfolio_id=?", (pid,))[0]["c"]
        ok = QMessageBox.question(
            self, "포트폴리오 삭제",
            f"'{self.pf_names.get(pid)}' 포트폴리오와 그 안의 종목 {n}개를 삭제할까요?")
        if ok == QMessageBox.Yes:
            self.db.x("DELETE FROM holdings WHERE portfolio_id=?", (pid,))
            self.db.x("DELETE FROM portfolios WHERE id=?", (pid,))
            self.load_portfolios(None)
            self.on_pf_changed()

    # ----- 종목 -----
    def on_buy(self):  # 추가
        dlg = HoldingDialog(self, list(self.pf_names.items()), self.pid())
        if not dlg.exec():
            return
        v = dlg.values()
        if v["qty"] <= 0:
            QMessageBox.warning(self, "확인", "수량을 입력해 주세요.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ticker, auto = normalize(v["market"], v["query"])
        finally:
            QApplication.restoreOverrideCursor()
        if ticker is None:
            QMessageBox.warning(self, "확인", "종목을 찾지 못했어요.\n"
                                "한국은 6자리 코드, 미국은 티커를 넣어 보세요.")
            return
        name = v["name"] or auto or ticker
        if not name:
            QMessageBox.warning(self, "확인", "티커가 없으면 표시 이름이 필요해요.")
            return
        cur = {"한국": "KRW", "미국": "USD"}.get(v["market"], v["cur"])
        found = self.db.q(
            "SELECT * FROM holdings WHERE portfolio_id=? AND "
            + ("ticker=?" if ticker else "name=?"),
            (v["pf"], ticker or name),
        )
        if found:  # 같은 포트폴리오에 이미 있으면 합치고 평균단가 재계산
            h = found[0]
            q0, a0 = h["qty"], h["avg"]
            new_avg = (q0 * a0 + v["qty"] * v["price"]) / (q0 + v["qty"])
            self.db.x("UPDATE holdings SET qty=?, avg=?, manual_price=COALESCE(?, manual_price)"
                      " WHERE id=?",
                      (q0 + v["qty"], new_avg, v["manual"] or None, h["id"]))
        else:
            self.db.x(
                "INSERT INTO holdings(category,market,name,ticker,currency,qty,avg,"
                "manual_price,portfolio_id,sort) VALUES(?,?,?,?,?,?,?,?,?,"
                "(SELECT COALESCE(MAX(sort),0)+1 FROM holdings))",
                (v["cat"], v["market"], name, ticker, cur, v["qty"], v["price"],
                 v["manual"] or None, v["pf"]),
            )
        self.render()
        self.refresh()

    def selected(self):
        r = self.table.currentRow()
        if r < 0 or r >= len(self.row_ids):
            QMessageBox.information(self, "안내", "표에서 종목을 먼저 선택해 주세요.")
            return None
        return self.db.q("SELECT * FROM holdings WHERE id=?", (self.row_ids[r],))[0]

    def on_edit(self):  # 수정
        h = self.selected()
        if not h:
            return
        dlg = HoldingDialog(self, list(self.pf_names.items()), None, h)
        if not dlg.exec():
            return
        v = dlg.values()
        if v["qty"] <= 0:
            QMessageBox.warning(self, "확인", "수량을 입력해 주세요.")
            return
        q = v["query"].strip()
        if q == (h["ticker"] or ""):  # 티커를 안 바꿨으면 그대로 사용
            ticker, auto = q, None
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                ticker, auto = normalize(v["market"], v["query"])
            finally:
                QApplication.restoreOverrideCursor()
        if ticker is None:
            QMessageBox.warning(self, "확인", "종목을 찾지 못했어요.\n"
                                "한국은 6자리 코드, 미국은 티커를 넣어 보세요.")
            return
        name = v["name"] or auto or ticker
        if not name:
            QMessageBox.warning(self, "확인", "티커가 없으면 표시 이름이 필요해요.")
            return
        cur = {"한국": "KRW", "미국": "USD"}.get(v["market"], v["cur"])
        self.db.x(
            "UPDATE holdings SET portfolio_id=?, category=?, market=?, name=?, ticker=?,"
            " currency=?, qty=?, avg=?, manual_price=? WHERE id=?",
            (v["pf"], v["cat"], v["market"], name, ticker, cur, v["qty"], v["price"],
             v["manual"] or None, h["id"]),
        )
        self.render()
        self.refresh()

    def on_category(self):
        h = self.selected()
        if not h:
            return
        cur = CATS.index(h["category"]) if h["category"] in CATS else 0
        item, ok = QInputDialog.getItem(
            self, "분류 변경", f"'{h['name']}'의 분류를 선택하세요", CATS, cur, False)
        if ok:
            self.db.x("UPDATE holdings SET category=? WHERE id=?", (item, h["id"]))
            self.render()

    def on_delete(self):
        h = self.selected()
        if not h:
            return
        ok = QMessageBox.question(self, "삭제", f"'{h['name']}'을(를) 목록에서 삭제할까요?")
        if ok == QMessageBox.Yes:
            self.db.x("DELETE FROM holdings WHERE id=?", (h["id"],))
            self.render()

    def closeEvent(self, e):
        self.save_cols()
        if self.worker and self.worker.isRunning():
            self.worker.wait(3000)
        if self.fund_worker and self.fund_worker.isRunning():
            self.fund_worker.wait(3000)
        super().closeEvent(e)


def main():
    import traceback

    def log_error(text):
        try:
            with open(os.path.join(APP_DIR, "error.log"), "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
        except Exception:
            pass

    def hook(t, v, tb):  # 창이 갑자기 닫히지 않고 원인을 보여 주도록
        log_error("".join(traceback.format_exception(t, v, tb)))
        try:
            QMessageBox.critical(
                None, "오류",
                f"예상치 못한 오류가 났어요.\n\n{v}\n\n"
                f"자세한 내용은 아래 파일에 저장했어요.\n{os.path.join(APP_DIR, 'error.log')}")
        except Exception:
            pass

    sys.excepthook = hook
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
