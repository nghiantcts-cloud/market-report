#!/usr/bin/env python3
"""
Báo cáo thị trường tài chính hàng ngày: vàng, USD, dầu, chứng khoán Việt Nam.

Cách dùng:
  python market_report.py --demo        # xem thử báo cáo bằng dữ liệu giả (không cần mạng)
  python market_report.py --check       # kiểm tra từng nguồn dữ liệu có lấy được không
  python market_report.py               # chạy 1 lần: thu thập -> tạo báo cáo -> gửi
  python market_report.py --loop        # chạy nền, tự gửi lúc 07:00 mỗi ngày (giờ VN)
  python market_report.py --site        # thêm: xuất trang web mobile vào thư mục docs/ (GitHub Pages)
"""
import argparse
import csv
import html
import os
import random
import smtplib
import ssl
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
BASE = Path(__file__).resolve().parent
REPORT_DIR = BASE / "reports"
HISTORY = BASE / "history.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; MarketReportBot/1.0)"}

NEWS_FEEDS = {
    "VnExpress - Kinh doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
    "CafeF - Chứng khoán": "https://cafef.vn/thi-truong-chung-khoan.rss",
}


# ----------------------------------------------------------------- tiện ích
def load_env():
    p = BASE / ".env"
    if not p.exists():
        return
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def vn_fmt(v, d=2):
    """25100.5 -> 25.100,50 (kiểu Việt Nam)"""
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def sign_pct(p):
    return "–" if p is None else f"{p:+.2f}%".replace(".", ",")


@dataclass
class Series:
    key: str
    name: str
    unit: str
    closes: list            # [(YYYY-MM-DD, giá)] tăng dần theo ngày
    source: str
    decimals: int = 2
    band: float = 1.0       # ngưỡng % (5 phiên) để coi là "đi ngang"
    note: str = ""

    @property
    def last(self):
        return self.closes[-1][1]

    def pct(self, n):
        if len(self.closes) <= n:
            return None
        return (self.closes[-1][1] / self.closes[-1 - n][1] - 1) * 100

    def trend(self):
        p5 = self.pct(5)
        if p5 is None:
            return "chưa đủ dữ liệu"
        vals = [c for _, c in self.closes[-20:]]
        above = self.last > mean(vals)
        if p5 > self.band and above:
            return "Tăng"
        if p5 < -self.band and not above:
            return "Giảm"
        return "Đi ngang"


# ------------------------------------------------------------ nguồn dữ liệu
def fetch_yahoo(symbol, key, name, unit, decimals=2, band=1.0):
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=False)
    df = df.dropna(subset=["Close"])
    if len(df) < 2:
        raise ValueError(f"Yahoo không trả đủ dữ liệu cho {symbol}")
    pts = [(i.strftime("%Y-%m-%d"), float(c)) for i, c in zip(df.index, df["Close"])]
    return Series(key, name, unit, pts, f"Yahoo Finance ({symbol})", decimals, band)


def fetch_vnindex_vndirect():
    r = requests.get(
        "https://finfo-api.vndirect.com.vn/v4/stock_prices",
        params={"q": "code:VNINDEX", "sort": "date", "size": 60},
        headers=UA, timeout=20,
    )
    r.raise_for_status()
    rows = r.json()["data"]
    pts = sorted((x["date"], float(x["close"])) for x in rows)
    if len(pts) < 2:
        raise ValueError("VNDirect không trả đủ dữ liệu")
    return Series("vnindex", "VN-Index", "điểm", pts, "VNDirect", 2, 1.0)


def fetch_vnindex_vnstock():
    from vnstock import Quote
    end = datetime.now(TZ)
    start = end - timedelta(days=90)
    df = Quote(symbol="VNINDEX", source="VCI").history(
        start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), interval="1D")
    pts = [(str(t)[:10], float(c)) for t, c in zip(df["time"], df["close"])]
    if len(pts) < 2:
        raise ValueError("vnstock không trả đủ dữ liệu")
    return Series("vnindex", "VN-Index", "điểm", pts, "vnstock (VCI)", 2, 1.0)


def fetch_vnindex():
    errs = []
    for fn in (fetch_vnindex_vndirect, fetch_vnindex_vnstock):
        try:
            return fn()
        except Exception as e:
            errs.append(f"{fn.__name__}: {e}")
    raise RuntimeError(" | ".join(errs))


def with_history(key, today, value):
    """Ghép giá hôm nay vào lịch sử đã lưu để tính xu hướng cho nguồn chỉ có giá hiện tại."""
    pts = []
    if HISTORY.exists():
        with HISTORY.open(encoding="utf-8") as f:
            pts = [(r["date"], float(r["value"])) for r in csv.DictReader(f) if r["key"] == key]
    pts = [p for p in pts if p[0] != today] + [(today, value)]
    return sorted(pts)


def fetch_vcb_usd(today):
    r = requests.get(
        "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx",
        params={"b": 10}, headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    for e in root.iter("Exrate"):
        if e.get("CurrencyCode") == "USD":
            sell = float(e.get("Sell").replace(",", ""))
            buy = float(e.get("Buy").replace(",", ""))
            return Series("usd_vcb", "USD Vietcombank (bán ra)", "VND",
                          with_history("usd_vcb", today, sell), "Vietcombank", 0, 0.3,
                          note=f"mua vào {vn_fmt(buy, 0)}")
    raise ValueError("Không thấy USD trong bảng tỷ giá Vietcombank")


def fetch_sjc(today):
    r = requests.post(
        "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx",
        data={"method": "GetCurrentGoldPricesByBranch", "BranchId": "1"},
        headers=UA, timeout=20)
    r.raise_for_status()
    rows = r.json()["data"]
    row = next((x for x in rows if "1L" in x.get("TypeName", "") or "SJC" in x.get("TypeName", "")), rows[0])
    sell, buy = float(row["SellValue"]), float(row["BuyValue"])
    return Series("gold_sjc", "Vàng SJC (bán ra)", "VND/lượng",
                  with_history("gold_sjc", today, sell), "SJC", 0, 1.0,
                  note=f"mua vào {vn_fmt(buy, 0)}")


def fetch_news(per_feed=5):
    out = {}
    for name, url in NEWS_FEEDS.items():
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            items = []
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                l = (it.findtext("link") or "").strip()
                if t:
                    items.append((t, l))
                if len(items) >= per_feed:
                    break
            out[name] = items
        except Exception as e:
            out[name] = [(f"(lỗi lấy tin: {e})", "")]
    return out


def collect():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    jobs = [
        ("gold_world", lambda: fetch_yahoo("GC=F", "gold_world", "Vàng thế giới", "USD/oz", 2, 1.0)),
        ("gold_sjc", lambda: fetch_sjc(today)),
        ("usd_vcb", lambda: fetch_vcb_usd(today)),
        ("usdvnd_mkt", lambda: fetch_yahoo("VND=X", "usdvnd_mkt", "USD/VND (liên ngân hàng)", "VND", 0, 0.3)),
        ("dxy", lambda: fetch_yahoo("DX-Y.NYB", "dxy", "Chỉ số USD (DXY)", "điểm", 2, 0.5)),
        ("oil_wti", lambda: fetch_yahoo("CL=F", "oil_wti", "Dầu WTI", "USD/thùng", 2, 2.0)),
        ("oil_brent", lambda: fetch_yahoo("BZ=F", "oil_brent", "Dầu Brent", "USD/thùng", 2, 2.0)),
        ("vnindex", fetch_vnindex),
    ]
    series, errors = {}, []
    for key, fn in jobs:
        try:
            series[key] = fn()
        except Exception as e:
            errors.append(f"{key}: {e}")
    return series, fetch_news(), errors


def demo_data():
    random.seed()

    def mk(key, name, unit, start, vol, dec, band):
        v, pts, d = start, [], datetime.now(TZ).date() - timedelta(days=60)
        for i in range(60):
            v *= 1 + random.gauss(0, vol)
            pts.append(((d + timedelta(days=i)).isoformat(), v))
        return Series(key, name, unit, pts, "DỮ LIỆU GIẢ (demo)", dec, band)

    s = {
        "gold_world": mk("gold_world", "Vàng thế giới", "USD/oz", 2650, 0.008, 2, 1.0),
        "gold_sjc": mk("gold_sjc", "Vàng SJC (bán ra)", "VND/lượng", 88_000_000, 0.004, 0, 1.0),
        "usd_vcb": mk("usd_vcb", "USD Vietcombank (bán ra)", "VND", 25_400, 0.001, 0, 0.3),
        "usdvnd_mkt": mk("usdvnd_mkt", "USD/VND (liên ngân hàng)", "VND", 25_300, 0.001, 0, 0.3),
        "dxy": mk("dxy", "Chỉ số USD (DXY)", "điểm", 104, 0.004, 2, 0.5),
        "oil_wti": mk("oil_wti", "Dầu WTI", "USD/thùng", 70, 0.015, 2, 2.0),
        "oil_brent": mk("oil_brent", "Dầu Brent", "USD/thùng", 74, 0.015, 2, 2.0),
        "vnindex": mk("vnindex", "VN-Index", "điểm", 1250, 0.009, 2, 1.0),
    }
    news = {"Demo": [("Tiêu đề tin mẫu số %d" % i, "") for i in range(1, 4)]}
    return s, news, []


# ----------------------------------------------------------------- phân tích
def gold_premium(S):
    """Chênh lệch giá vàng SJC so với vàng thế giới quy đổi (VND/lượng)."""
    if "gold_world" not in S or "gold_sjc" not in S:
        return None
    rate = S["usd_vcb"].last if "usd_vcb" in S else (S["usdvnd_mkt"].last if "usdvnd_mkt" in S else None)
    if not rate:
        return None
    world_luong = S["gold_world"].last * rate * 37.5 / 31.1035
    diff = S["gold_sjc"].last - world_luong
    return world_luong, diff, diff / world_luong * 100


def commentary(S):
    lines = []
    for k in ("gold_world", "gold_sjc", "usd_vcb", "usdvnd_mkt", "dxy", "oil_wti", "oil_brent", "vnindex"):
        if k not in S:
            continue
        s = S[k]
        lines.append(
            f"• {s.name}: {vn_fmt(s.last, s.decimals)} {s.unit} "
            f"({sign_pct(s.pct(1))} so với phiên trước; 5 phiên {sign_pct(s.pct(5))}; xu hướng: {s.trend()})")
    gp = gold_premium(S)
    if gp:
        w, d, p = gp
        sg = "+" if d >= 0 else "-"
        lines.append(f"• Chênh lệch vàng SJC so với vàng thế giới quy đổi: {sg}{vn_fmt(abs(d), 0)} VND/lượng "
                     f"({sg}{vn_fmt(abs(p), 1)}%); vàng thế giới quy đổi ≈ {vn_fmt(w, 0)} VND/lượng.")
    notes = []
    if "dxy" in S and "gold_world" in S:
        if S["dxy"].trend() == "Tăng" and S["gold_world"].trend() == "Giảm":
            notes.append("USD toàn cầu mạnh lên đang gây áp lực lên giá vàng.")
        elif S["dxy"].trend() == "Giảm" and S["gold_world"].trend() == "Tăng":
            notes.append("USD yếu đi đang hỗ trợ giá vàng tăng.")
    if "oil_brent" in S and S["oil_brent"].trend() == "Tăng":
        notes.append("Giá dầu tăng có thể gây áp lực lên chi phí và lạm phát.")
    if "vnindex" in S:
        v = S["vnindex"]
        ma20 = mean(c for _, c in v.closes[-20:])
        notes.append(f"VN-Index đang {'trên' if v.last > ma20 else 'dưới'} đường trung bình 20 phiên "
                     f"({vn_fmt(ma20, 0)} điểm).")
    if notes:
        lines.append("")
        lines.append("Nhận định tự động (chỉ mang tính tham khảo, không phải khuyến nghị đầu tư):")
        lines += [f"  - {n}" for n in notes]
    return lines


# ------------------------------------------------------------------ báo cáo
def spark(vals, w=140, h=32, color="#2563eb"):
    vals = vals[-30:]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - (v - lo) / rng * (h - 4) - 2:.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{pts}"/></svg>')


def pct_cell(p, extra=""):
    cls = "" if p is None else ("up" if p > 0 else "down" if p < 0 else "")
    return f'<td class="num {cls} {extra}">{sign_pct(p)}</td>'


def build_html(S, news, errors, now, root=None):
    head_extra, nav = "", ""
    if root is not None:   # chế độ trang web: thêm thẻ cho iPhone "Thêm vào Màn hình chính"
        head_extra = (f'<link rel="apple-touch-icon" href="{root}apple-touch-icon.png">'
                      '<meta name="apple-mobile-web-app-capable" content="yes">'
                      '<meta name="mobile-web-app-capable" content="yes">'
                      '<meta name="apple-mobile-web-app-title" content="Thị trường">'
                      '<meta name="theme-color" content="#2563eb">')
        nav = (f'<p><a href="{root}index.html">Mới nhất</a> · '
               f'<a href="{root}archive.html">Các ngày trước</a></p>')
    rows = []
    for k in ("gold_world", "gold_sjc", "usd_vcb", "usdvnd_mkt", "dxy", "oil_wti", "oil_brent", "vnindex"):
        if k not in S:
            continue
        s = S[k]
        t = s.trend()
        tcls = {"Tăng": "up", "Giảm": "down"}.get(t, "")
        note = f'<br><small>{html.escape(s.note)}</small>' if s.note else ""
        rows.append(
            f"<tr><td><b>{html.escape(s.name)}</b>{note}<br><small>{html.escape(s.source)}</small></td>"
            f'<td class="num"><b>{vn_fmt(s.last, s.decimals)}</b><br><small>{html.escape(s.unit)}</small></td>'
            f"{pct_cell(s.pct(1))}{pct_cell(s.pct(5))}{pct_cell(s.pct(20), "hide-sm")}"
            f'<td class="{tcls}"><b>{t}</b></td><td class="hide-sm">{spark([c for _, c in s.closes])}</td></tr>')
    news_html = ""
    for src, items in news.items():
        li = "".join(
            f'<li><a href="{html.escape(l)}">{html.escape(t)}</a></li>' if l else f"<li>{html.escape(t)}</li>"
            for t, l in items)
        news_html += f"<h3>{html.escape(src)}</h3><ul>{li}</ul>"
    comm = "".join(f"<p>{html.escape(x)}</p>" if x else "" for x in commentary(S))
    err_html = ""
    if errors:
        err_html = ("<h3>Nguồn không lấy được</h3><ul>"
                    + "".join(f"<li>{html.escape(e)}</li>" for e in errors) + "</ul>")
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Báo cáo thị trường {now:%d/%m/%Y}</title>{head_extra}
<style>
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#111;line-height:1.5}}
h1{{margin-bottom:0}} .sub{{color:#666;margin-top:4px}}
table{{border-collapse:collapse;width:100%;margin:16px 0}}
th,td{{padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:middle}}
th{{background:#f3f4f6;font-size:13px}} .num{{text-align:right;white-space:nowrap}}
.up{{color:#15803d}} .down{{color:#b91c1c}} small{{color:#6b7280}}
.overflow{{overflow-x:auto}}
@media (max-width:600px){{.hide-sm{{display:none}}th,td{{padding:6px;font-size:14px}}body{{margin:12px auto}}}}
.disc{{font-size:12px;color:#6b7280;margin-top:32px}}
@media (prefers-color-scheme:dark){{body{{background:#111827;color:#e5e7eb}}th{{background:#1f2937}}
td,th{{border-color:#374151}}.up{{color:#4ade80}}.down{{color:#f87171}}small,.sub,.disc{{color:#9ca3af}}a{{color:#60a5fa}}}}
</style></head><body>
<h1>Báo cáo thị trường tài chính</h1>
<div class="sub">Cập nhật lúc {now:%H:%M, %d/%m/%Y} (giờ Việt Nam)</div>{nav}
<div class="overflow"><table>
<tr><th>Chỉ tiêu</th><th class="num">Giá gần nhất</th><th class="num">1 phiên</th><th class="num">5 phiên</th>
<th class="num hide-sm">20 phiên</th><th>Xu hướng</th><th class="hide-sm">30 phiên</th></tr>
{''.join(rows)}</table></div>
<h2>Nhận định</h2>{comm}
<h2>Tin kinh tế nổi bật</h2>{news_html}
{err_html}
<p class="disc">Báo cáo tạo tự động từ các nguồn công khai, có thể chậm hoặc sai lệch. Nội dung chỉ mang tính
tham khảo, không phải lời khuyên đầu tư.</p>
</body></html>"""


def build_text(S, news, errors, now):
    out = [f"BÁO CÁO THỊ TRƯỜNG - {now:%H:%M %d/%m/%Y}", ""]
    out += commentary(S)
    out.append("")
    for src, items in news.items():
        out.append(f"Tin {src}:")
        out += [f"  - {t}" for t, _ in items]
    if errors:
        out += ["", "Nguồn không lấy được:"] + [f"  - {e}" for e in errors]
    out += ["", "Chi tiết xem file HTML đính kèm. Chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."]
    return "\n".join(out)


# ------------------------------------------------------------------- gửi đi
def send_email(subject, text, attach: Path):
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "587"))
    user, pwd = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASS", "")
    to = [x.strip() for x in os.environ["MAIL_TO"].split(",") if x.strip()]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.getenv("MAIL_FROM", user), ", ".join(to)
    msg.set_content(text)
    msg.add_attachment(attach.read_bytes(), maintype="text", subtype="html", filename=attach.name)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            if user:
                s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            if user:
                s.login(user, pwd)
            s.send_message(msg)


def send_telegram(text, attach: Path, page_url=None):
    tok, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    api = f"https://api.telegram.org/bot{tok}"
    if page_url:   # có trang web: gửi tóm tắt + link (mở nhanh trên iPhone)
        r = requests.post(f"{api}/sendMessage", timeout=30, data={
            "chat_id": chat, "text": text[:3600] + f"\n\n📊 Báo cáo đầy đủ: {page_url}"})
        r.raise_for_status()
        return
    with attach.open("rb") as f:
        r = requests.post(f"{api}/sendDocument", data={"chat_id": chat, "caption": text[:1000]},
                          files={"document": (attach.name, f)}, timeout=60)
    r.raise_for_status()


def notify_error(msg):
    try:
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": "⚠️ " + msg[:500]}, timeout=20)
    except Exception:
        pass


def deliver(subject, text, path, page_url=None):
    sent = False
    if os.getenv("SMTP_HOST") and os.getenv("MAIL_TO"):
        mail_text = text + (f"\n\nXem trên điện thoại: {page_url}" if page_url else "")
        send_email(subject, mail_text, path)
        print("Đã gửi email.")
        sent = True
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        send_telegram(text, path, page_url)
        print("Đã gửi Telegram.")
        sent = True
    if not sent:
        print("Chưa cấu hình email/Telegram trong .env nên chỉ lưu file:", path)


def save_history(S, today):
    seen = set()
    if HISTORY.exists():
        with HISTORY.open(encoding="utf-8") as f:
            seen = {(r["date"], r["key"]) for r in csv.DictReader(f)}
    new = not HISTORY.exists()
    with HISTORY.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "key", "value"])
        for k, s in S.items():
            if (today, k) not in seen:
                w.writerow([today, k, s.last])


# ------------------------------------------------------------- trang web (mobile)
def make_icon(path, size=180):
    """Tạo icon PNG đơn giản (cột biểu đồ trắng trên nền xanh) cho iPhone, không cần thư viện ngoài."""
    import struct
    import zlib
    bg, fg = (37, 99, 235), (255, 255, 255)
    px = [[bg] * size for _ in range(size)]
    bw = size // 9
    gap = bw // 2
    x0 = (size - (5 * bw + 4 * gap)) // 2
    base = int(size * 0.78)
    for i, h in enumerate((0.35, 0.5, 0.45, 0.7, 0.85)):
        top = base - int(size * 0.6 * h)
        for y in range(top, base):
            for x in range(x0 + i * (bw + gap), x0 + i * (bw + gap) + bw):
                px[y][x] = fg
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def write_site(S, news, errors, now):
    docs = BASE / "docs"
    (docs / "archive").mkdir(parents=True, exist_ok=True)
    day = now.strftime("%Y-%m-%d")
    (docs / "index.html").write_text(build_html(S, news, errors, now, root=""), encoding="utf-8")
    (docs / "archive" / f"{day}.html").write_text(build_html(S, news, errors, now, root="../"), encoding="utf-8")
    pages = sorted((docs / "archive").glob("*.html"), reverse=True)
    li = "".join(f'<li><a href="archive/{p.name}">{p.stem[8:]}/{p.stem[5:7]}/{p.stem[:4]}</a></li>' for p in pages)
    (docs / "archive.html").write_text(f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Các báo cáo trước</title>
<link rel="apple-touch-icon" href="apple-touch-icon.png"><meta name="apple-mobile-web-app-capable" content="yes">
<style>body{{font-family:system-ui,Arial,sans-serif;max-width:600px;margin:16px auto;padding:0 16px;line-height:2}}
@media (prefers-color-scheme:dark){{body{{background:#111827;color:#e5e7eb}}a{{color:#60a5fa}}}}</style></head>
<body><h1>Các báo cáo trước</h1><p><a href="index.html">← Mới nhất</a></p><ul>{li}</ul></body></html>""", encoding="utf-8")
    if not (docs / "apple-touch-icon.png").exists():
        make_icon(docs / "apple-touch-icon.png")
    (docs / ".nojekyll").write_text("")
    print("Đã cập nhật trang web:", docs / "index.html")


# ---------------------------------------------------------------------- chạy
def run_once(demo=False, send=True, site=False):
    now = datetime.now(TZ)
    S, news, errors = demo_data() if demo else collect()
    if not S:
        raise RuntimeError("Không lấy được dữ liệu từ nguồn nào: " + "; ".join(errors))
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"bao-cao-thi-truong-{now:%Y-%m-%d}{'-demo' if demo else ''}.html"
    path.write_text(build_html(S, news, errors, now), encoding="utf-8")
    print("Đã tạo báo cáo:", path)
    if errors:
        print("Nguồn lỗi:", *errors, sep="\n  - ")
    page_url = None
    if site:
        write_site(S, news, errors, now)
        page_url = os.getenv("PAGE_URL", "").strip() or None
    if not demo:
        save_history(S, now.strftime("%Y-%m-%d"))
        if send:
            deliver(f"Báo cáo thị trường {now:%d/%m/%Y}", build_text(S, news, errors, now), path, page_url)
    return path


def check():
    S, news, errors = collect()
    for k, s in S.items():
        print(f"[OK ] {k:<11} {vn_fmt(s.last, s.decimals)} {s.unit}  ({len(s.closes)} điểm, nguồn {s.source})")
    for e in errors:
        print(f"[LỖI] {e}")
    for src, items in news.items():
        print(f"[TIN] {src}: {len(items)} bài")


def loop(run_at):
    hh, mm = map(int, run_at.split(":"))
    print(f"Chạy nền: gửi báo cáo lúc {run_at} mỗi ngày (giờ VN). Nhấn Ctrl+C để dừng.")
    while True:
        now = datetime.now(TZ)
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        time.sleep((nxt - now).total_seconds())
        try:
            run_once()
        except Exception as e:
            traceback.print_exc()
            notify_error(f"Lỗi tạo báo cáo: {e}")


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser(description="Báo cáo thị trường tài chính hàng ngày")
    ap.add_argument("--demo", action="store_true", help="tạo báo cáo bằng dữ liệu giả để xem thử")
    ap.add_argument("--check", action="store_true", help="kiểm tra các nguồn dữ liệu")
    ap.add_argument("--loop", action="store_true", help="chạy nền, gửi đúng giờ mỗi ngày")
    ap.add_argument("--no-send", action="store_true", help="chỉ tạo file, không gửi")
    ap.add_argument("--site", action="store_true", help="xuất thêm trang web mobile vào docs/ (GitHub Pages)")
    a = ap.parse_args()
    if a.check:
        check()
    elif a.loop:
        loop(os.getenv("RUN_AT", "07:00"))
    else:
        try:
            run_once(demo=a.demo, send=not a.no_send, site=a.site)
        except Exception as e:
            traceback.print_exc()
            if not a.demo:
                notify_error(f"Lỗi tạo báo cáo: {e}")
            sys.exit(1)
