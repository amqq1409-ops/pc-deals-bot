from __future__ import annotations

# صائد صفقات البي سي — ملف واحد. الإعدادات في config.yaml


# ===== models.py =====

import re
from dataclasses import dataclass, field
from typing import Optional

STORE_NAMES_AR = {
    "amazon_sa": "أمازون السعودية",
    "noon": "نون",
    "jarir": "مكتبة جرير",
    "extra": "إكسترا",
}

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def parse_price(text: str | float | int | None) -> Optional[float]:
    """'SAR 1,299.00' / '١٢٩٩ ر.س' / 1299 -> 1299.0"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    s = str(text).translate(_AR_DIGITS)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    try:
        val = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return val if val > 0 else None


@dataclass
class Product:
    store: str
    title: str
    url: str
    price: float
    original_price: Optional[float] = None
    rating: Optional[float] = None
    reviews: int = 0
    image: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    category: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    historical_low: bool = False
    tier: str = ""
    season: str = ""
    imported: bool = False

    @property
    def key(self) -> str:
        return f"{self.store}:{self.sku or self.url}"

    @property
    def discount_pct(self) -> float:
        if self.original_price and self.original_price > self.price:
            return round((1 - self.price / self.original_price) * 100, 1)
        return 0.0


# ===== scrapers/base.py =====

import logging
import random
import time
from typing import List

import requests


log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
]


class BaseScraper:
    name = "base"

    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self.deadline = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        })

    def get(self, url: str, **kw) -> requests.Response | None:
        for attempt in range(3):
            if self.deadline and time.monotonic() > self.deadline:
                log.warning("%s: انتهت ميزانية الوقت لهذا المتجر", self.name)
                return None
            try:
                time.sleep(self.delay + random.random())
                r = self.session.get(url, timeout=15, **kw)
                if r.status_code == 200 and len(r.text) > 2000:
                    return r
                if r.status_code == 403:
                    log.warning("%s: HTTP 403 (محظور) — لا فائدة من إعادة المحاولة", self.name)
                    return None
                log.warning("%s: HTTP %s (محاولة %d) %s", self.name, r.status_code, attempt + 1, url)
            except requests.RequestException as e:
                log.warning("%s: خطأ شبكة %s", self.name, e)
            time.sleep(3 * (attempt + 1))
        return None

    def search(self, query: str) -> List[Product]:
        raise NotImplementedError


# ===== scrapers/amazon_sa.py =====

import re
from typing import List
from urllib.parse import quote_plus

from bs4 import BeautifulSoup


class AmazonSA(BaseScraper):
    name = "amazon_sa"
    BASE = "https://www.amazon.sa"
    rate = 1.0
    imported = False

    def search(self, query: str) -> List[Product]:
        url = f"{self.BASE}/s?k={quote_plus(query)}&language=en_AE"
        r = self.get(url)
        if not r:
            return []
        return self.parse(r.text)

    def parse(self, html: str) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Product] = []
        for card in soup.select('div[data-component-type="s-search-result"]'):
            asin = card.get("data-asin")
            if not asin:
                continue
            title_el = card.select_one("h2 span") or card.select_one("h2")
            price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
            if not title_el or not price_el:
                continue
            price = parse_price(price_el.get_text())
            if not price:
                continue
            old_el = card.select_one("span.a-price.a-text-price span.a-offscreen")
            original = parse_price(old_el.get_text()) if old_el else None

            rating = None
            rating_el = card.select_one("span.a-icon-alt")
            if rating_el:
                m = re.search(r"(\d+(?:[.,]\d+)?)", rating_el.get_text())
                if m:
                    rating = float(m.group(1).replace(",", "."))

            reviews = 0
            rev_el = card.select_one('span[aria-label*="ratings"], a[href*="customerReviews"] span')
            if rev_el:
                txt = (rev_el.get("aria-label") or rev_el.get_text()).lower()
                m = re.search(r"([\d.,]+)\s*([km])?", txt)
                if m:
                    n = float(m.group(1).replace(",", ""))
                    reviews = int(n * {"k": 1000, "m": 1_000_000}.get(m.group(2), 1))

            img = card.select_one("img.s-image")
            out.append(Product(
                store=self.name,
                title=title_el.get_text(strip=True),
                url=f"{self.BASE}/dp/{asin}",
                price=price,
                original_price=original,
                rating=rating,
                reviews=reviews,
                image=img.get("src") if img else None,
                sku=asin,
            ))
        if self.rate != 1.0:
            for p in out:
                p.price = round(p.price * self.rate, 2)
                p.original_price = round(p.original_price * self.rate, 2) if p.original_price else None
        for p in out:
            p.imported = self.imported
        return out


class AmazonGlobal(AmazonSA):
    def __init__(self, name: str, base: str, rate: float):
        super().__init__()
        self.name = name
        self.BASE = base.rstrip("/")
        self.rate = rate
        self.imported = True

    def search(self, query: str) -> List[Product]:
        r = self.get(f"{self.BASE}/s?k={quote_plus(query)}")
        return self.parse(r.text) if r else []


# ===== scrapers/noon.py =====

import json
import re
from typing import Any, Iterator, List
from urllib.parse import quote_plus

from bs4 import BeautifulSoup


def _walk(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        if "name" in node and ("sale_price" in node or "price" in node) and ("sku" in node or "catalog_sku" in node):
            yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


class Noon(BaseScraper):
    name = "noon"
    BASE = "https://www.noon.com"

    def search(self, query: str) -> List[Product]:
        url = f"{self.BASE}/saudi-en/search/?q={quote_plus(query)}"
        r = self.get(url)
        if not r:
            return []
        return self.parse(r.text)

    def parse(self, html: str) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        raw = tag.string if tag and tag.string else None
        if not raw:
            m = re.search(r'__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
            raw = m.group(1) if m else None
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        seen, out = set(), []
        for h in _walk(data):
            sku = str(h.get("sku") or h.get("catalog_sku"))
            if sku in seen:
                continue
            seen.add(sku)
            sale = parse_price(h.get("sale_price"))
            reg = parse_price(h.get("price"))
            price = sale or reg
            if not price:
                continue
            original = reg if (sale and reg and reg > sale) else None

            slug = h.get("url") or h.get("slug") or ""
            if slug.startswith("http"):
                link = slug
            else:
                link = f"{self.BASE}/saudi-en/{slug.lstrip('/')}" if slug else f"{self.BASE}/saudi-en/p/{sku}"
                if slug and "/p/" not in link:
                    link = f"{self.BASE}/saudi-en/{slug.strip('/')}/{sku}/p/"

            img_key = h.get("image_key")
            image = f"https://f.nooncdn.com/p/{img_key}.jpg" if img_key else None
            rating = h.get("product_rating")
            if isinstance(rating, dict):
                rating = rating.get("value") or rating.get("average")
            reviews = h.get("product_rating", {}).get("count", 0) if isinstance(h.get("product_rating"), dict) else 0
            try:
                rating = float(rating) if rating else None
            except (TypeError, ValueError):
                rating = None

            out.append(Product(
                store=self.name,
                title=str(h.get("name")),
                url=link,
                price=price,
                original_price=original,
                rating=rating,
                reviews=int(reviews or 0),
                image=image,
                sku=sku,
                brand=h.get("brand"),
            ))
        return out


# ===== scrapers/jarir.py =====

from typing import List
from urllib.parse import quote_plus

from bs4 import BeautifulSoup


class Jarir(BaseScraper):
    name = "jarir"
    BASE = "https://www.jarir.com"

    def search(self, query: str) -> List[Product]:
        url = f"{self.BASE}/sa-en/catalogsearch/result/?q={quote_plus(query)}"
        r = self.get(url)
        if not r:
            return []
        return self.parse(r.text)

    def parse(self, html: str) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Product] = []
        for card in soup.select("li.product-item, div.product-item, div.product-item-info"):
            link = card.select_one("a.product-item-link, a.product-item-photo, h2 a, a[href]")
            if not link:
                continue
            title = link.get_text(strip=True) or (card.select_one("img") or {}).get("alt", "")
            href = link.get("href", "")
            if not title or not href:
                continue
            if href.startswith("/"):
                href = self.BASE + href

            price_el = (card.select_one("[data-price-type='finalPrice'] .price")
                        or card.select_one("span.special-price .price")
                        or card.select_one(".price-box .price"))
            old_el = (card.select_one("[data-price-type='oldPrice'] .price")
                      or card.select_one("span.old-price .price"))
            price = parse_price(price_el.get_text()) if price_el else None
            if not price:
                continue
            original = parse_price(old_el.get_text()) if old_el else None

            img = card.select_one("img")
            sku = card.get("data-sku") or card.get("data-product-id") or href.rstrip("/").split("/")[-1]
            out.append(Product(
                store=self.name, title=title, url=href, price=price,
                original_price=original, image=(img.get("src") if img else None), sku=str(sku),
            ))
        return out


# ===== scrapers/extra.py =====

import json
from typing import Any, Iterator, List
from urllib.parse import quote_plus

from bs4 import BeautifulSoup


def _ld_products(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        t = node.get("@type")
        if t == "Product" or (isinstance(t, list) and "Product" in t):
            yield node
        for v in node.values():
            yield from _ld_products(v)
    elif isinstance(node, list):
        for v in node:
            yield from _ld_products(v)


class Extra(BaseScraper):
    name = "extra"
    BASE = "https://www.extra.com"

    def search(self, query: str) -> List[Product]:
        r = self.get(f"{self.BASE}/en-sa/search/?text={quote_plus(query)}")
        return self.parse(r.text) if r else []

    def parse(self, html: str) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        out: List[Product] = []
        seen = set()
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except json.JSONDecodeError:
                continue
            for p in _ld_products(data):
                offers = p.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = parse_price(offers.get("price") or offers.get("lowPrice"))
                url = p.get("url") or offers.get("url")
                if not (price and url and p.get("name")) or url in seen:
                    continue
                seen.add(url)
                agg = p.get("aggregateRating") or {}
                img = p.get("image")
                if isinstance(img, list):
                    img = img[0] if img else None
                brand = p.get("brand")
                out.append(Product(
                    store=self.name, title=str(p["name"]),
                    url=url if url.startswith("http") else self.BASE + url,
                    price=price, original_price=None,
                    rating=float(agg["ratingValue"]) if agg.get("ratingValue") else None,
                    reviews=int(float(agg.get("reviewCount") or agg.get("ratingCount") or 0)),
                    image=img if isinstance(img, str) else None,
                    sku=str(p.get("sku") or url),
                    brand=brand.get("name") if isinstance(brand, dict) else brand,
                ))
        return out


class ShopifyStore(BaseScraper):
    def __init__(self, name: str, base: str, rate: float = 1.0, imported: bool = False):
        super().__init__()
        self.name = name
        self.BASE = base.rstrip("/")
        self.rate = rate
        self.imported = imported

    def search(self, query: str) -> List[Product]:
        url = (f"{self.BASE}/search/suggest.json?q={quote_plus(query)}"
               f"&resources[type]=product&resources[limit]=10&resources[options][unavailable_products]=hide")
        if self.deadline and time.monotonic() > self.deadline:
            return []
        try:
            time.sleep(self.delay + random.random())
            r = self.session.get(url, timeout=15, headers={"Accept": "application/json"})
            if r.status_code != 200:
                log.warning("%s: HTTP %s", self.name, r.status_code)
                return []
            return self.parse(r.text)
        except (requests.RequestException, ValueError) as e:
            log.warning("%s: خطأ %s", self.name, e)
            return []

    def parse(self, text: str) -> List[Product]:
        data = json.loads(text)
        items = (((data.get("resources") or {}).get("results") or {}).get("products")) or []
        out: List[Product] = []
        for it in items:
            price = parse_price(it.get("price"))
            if not price or not it.get("title") or not it.get("url"):
                continue
            was = parse_price(it.get("compare_at_price_max") or it.get("compare_at_price_min"))
            if was is not None and was <= price:
                was = None
            img = it.get("image") or (it.get("featured_image") or {}).get("url")
            out.append(Product(
                store=self.name, title=str(it["title"]), url=self.BASE + str(it["url"]).split("?")[0],
                price=round(price * self.rate, 2),
                original_price=round(was * self.rate, 2) if was else None, rating=None, reviews=0,
                image=img if isinstance(img, str) else None,
                sku=str(it.get("id") or it["url"]), brand=it.get("vendor"), imported=self.imported,
            ))
        return out


class JsonLdStore(Extra):
    def __init__(self, name: str, base: str, search_path: str, rate: float = 1.0, imported: bool = False):
        super().__init__()
        self.name = name
        self.BASE = base.rstrip("/")
        self.search_path = search_path
        self.rate = rate
        self.imported = imported

    def search(self, query: str) -> List[Product]:
        r = self.get(self.BASE + self.search_path.replace("{q}", quote_plus(query)))
        if not r:
            return []
        out = self.parse(r.text)
        for p in out:
            p.price = round(p.price * self.rate, 2)
            p.imported = self.imported
        return out


class SteamSpecials(BaseScraper):
    name = "steam"

    def search(self, query: str) -> List[Product]:
        if query != "specials":
            return []
        if self.deadline and time.monotonic() > self.deadline:
            return []
        try:
            r = self.session.get("https://store.steampowered.com/api/featuredcategories?cc=sa&l=english", timeout=15)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("steam: خطأ %s", e)
            return []
        out: List[Product] = []
        for sec in ("specials", "top_sellers", "new_releases"):
            for it in (data.get(sec) or {}).get("items", []):
                disc, fin, orig = it.get("discount_percent", 0), it.get("final_price"), it.get("original_price")
                if not (it.get("name") and fin and orig and disc and it.get("id")):
                    continue
                out.append(Product(
                    store="steam", title=it["name"], url=f"https://store.steampowered.com/app/{it['id']}/",
                    price=fin / 100, original_price=orig / 100, image=it.get("large_capsule_image"),
                    sku=str(it["id"]), category="games", tier="basic"))
        return out


# ===== scoring.py =====

import math
import statistics
from typing import Dict, List, Optional


def classify(p: Product, categories: dict) -> Optional[str]:
    if p.category and p.category in categories:
        return p.category
    t = p.title.lower()
    for name, c in categories.items():
        if not any(k.lower() in t for k in c["include_any"]):
            continue
        if any(k.lower() in t for k in c.get("exclude_any", [])):
            continue
        if not (c.get("min_price", 0) <= p.price <= c.get("max_price", 10**9)):
            continue
        return name
    return None


def find_brand(p: Product, trusted: List[str]) -> Optional[str]:
    hay = f"{p.brand or ''} {p.title}".lower()
    for b in trusted:
        if b.lower() in hay:
            return b
    return None


def score_product(p: Product, cfg: dict, history: List[float]) -> Product:
    w = cfg["weights"]
    reasons: List[str] = []

    d = p.discount_pct
    s_disc = min(d, 70) / 70 * w["discount"]

    if p.rating:
        s_rate = max(0.0, min(1.0, (p.rating - 3.5) / 1.5)) * w["rating"]
    else:
        s_rate = 0.0

    s_rev = min(1.0, math.log10(p.reviews + 1) / 3) * w["reviews"] if p.reviews else 0.0

    brand = find_brand(p, cfg["trusted_brands"])
    s_brand = w["brand"] if brand else 0.0
    if brand:
        p.brand = p.brand or brand
        reasons.append(f"ماركة موثوقة ({brand})")

    s_hist = 0.0
    if len(history) >= 3:
        low = min(history)
        med = statistics.median(history)
        if p.price <= low:
            s_hist = w["price_history"]
            p.historical_low = True
            reasons.append("أقل سعر سجّلناه")
        elif p.price < med * 0.9:
            s_hist = w["price_history"] * 0.6
            reasons.append("أقل من متوسط سعره المعتاد")
        elif p.price > med * 1.05 and d >= 30:
            s_hist = -w["price_history"]
            reasons.append("⚠️ السعر أعلى من المعتاد رغم الخصم المعلن")

    if d >= 50:
        reasons.append(f"خصم كبير {d:.0f}%")
    if p.rating and p.rating >= 4.5 and p.reviews >= 100:
        reasons.append("تقييمات ممتازة")

    p.score = round(max(0.0, s_disc + s_rate + s_rev + s_brand + s_hist), 1)
    p.reasons = reasons
    return p


TIER_RANK = {"premium": 0, "high": 1, "basic": 2}


def assign_tier(p: Product, cfg: dict) -> str:
    if p.category == "games":
        p.tier = "basic"
        return p.tier
    cat = cfg["categories"].get(p.category) or {}
    t = p.title.lower()
    brand = find_brand(p, cfg["trusted_brands"])
    ref = max(p.price, p.original_price or 0)
    if any(k.lower() in t for k in cat.get("premium_keywords", [])):
        p.tier = "premium"
    elif brand and cat.get("premium_ref_price") and ref >= cat["premium_ref_price"]:
        p.tier = "premium"
    elif brand:
        p.tier = "high"
    else:
        p.tier = "basic"
    return p.tier


def is_good_deal(p: Product, cfg: dict) -> bool:
    d = p.discount_pct
    if p.category == "games":
        return d >= cfg.get("games_min_discount", 70) and (p.original_price or 0) >= cfg.get("games_min_original", 100)
    tier = p.tier or ("high" if find_brand(p, cfg["trusted_brands"]) else "basic")
    need = cfg.get("tier_min_discount", {}).get(tier, cfg["min_discount_pct"])
    if d < need:
        return False
    if tier == "basic" and not (p.rating and p.rating >= cfg.get("basic_min_rating", 4.2)
                                and p.reviews >= cfg.get("basic_min_reviews", 50)):
        return False
    rated = p.rating is not None and p.reviews >= cfg["min_reviews"]
    if rated and p.rating < cfg["min_rating"]:
        return False
    if not rated:
        if not find_brand(p, cfg["trusted_brands"]):
            return False
        if d > cfg["max_discount_suspicious"]:
            return False
    if d > cfg["max_discount_suspicious"] and p.reviews < cfg["min_reviews"]:
        return False
    return p.score >= cfg["min_score"]


# ===== store.py =====

import json
import time
from pathlib import Path
from typing import Dict, List


STATE_PATH = Path(__file__).resolve().parent / "data" / "state.json"
MAX_HISTORY = 60
HISTORY_TTL_DAYS = 120


class State:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.data: Dict = {"history": {}, "sent": {}, "warned": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text("utf-8")))
            except json.JSONDecodeError:
                pass

    def history(self, key: str) -> List[float]:
        return [p for _, p in self.data["history"].get(key, [])]

    def record(self, p: Product) -> None:
        h = self.data["history"].setdefault(p.key, [])
        now = int(time.time())
        if not h or now - h[-1][0] > 6 * 3600:
            h.append([now, p.price])
        del h[:-MAX_HISTORY]

    def should_send(self, p: Product, drop_pct: float) -> bool:
        last = self.data["sent"].get(p.key)
        if not last:
            return True
        return p.price <= last["price"] * (1 - drop_pct / 100)

    def mark_sent(self, p: Product) -> None:
        self.data["sent"][p.key] = {"price": p.price, "ts": int(time.time())}

    def should_warn(self, store: str) -> bool:
        today = time.strftime("%Y-%m-%d")
        if self.data["warned"].get(store) == today:
            return False
        self.data["warned"][store] = today
        return True

    def save(self) -> None:
        cutoff = time.time() - HISTORY_TTL_DAYS * 86400
        self.data["history"] = {k: v for k, v in self.data["history"].items() if v and v[-1][0] > cutoff}
        self.data["sent"] = {k: v for k, v in self.data["sent"].items() if v["ts"] > cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), "utf-8")


# ===== telegram.py =====

import html
import logging
import os

import requests


log = logging.getLogger(__name__)

TIER_LABELS = {"premium": "💎 فئة ممتازة", "high": "⭐ ماركة موثوقة", "basic": "🔹 فئة أساسية (خصم كبير)"}


def format_message(p: Product, cat_label: str, emoji: str = "🔥") -> str:
    e = html.escape
    lines = [f"{emoji} <b>صفقة {e(cat_label)}</b>  |  🎯 {p.score:.0f}/100"]
    if p.tier:
        lines.append(TIER_LABELS.get(p.tier, ""))
    if p.season:
        lines.append(f"🛍️ موسم: {e(p.season)}")
    lines.append("")
    lines.append(f"<b>{e(p.title[:140])}</b>")
    lines.append(f"🏪 {STORE_NAMES_AR.get(p.store, p.store)}")
    price = f"💰 <b>{p.price:,.0f} ر.س</b>"
    if p.original_price:
        price += f"  بدل <s>{p.original_price:,.0f}</s>"
    lines.append(price)
    if p.discount_pct:
        lines.append(f"📉 خصم <b>{p.discount_pct:.0f}%</b>")
    if p.rating:
        lines.append(f"⭐ {p.rating:.1f} ({p.reviews:,} تقييم)")
    for r in p.reasons:
        lines.append(f"✅ {e(r)}" if not r.startswith("⚠️") else e(r))
    if p.imported:
        lines.append("🌍 شحن دولي: أضف الجمارك وضريبة القيمة المضافة، وتأكد أن المتجر يشحن للسعودية وأن الضمان يُغطي المنتج")
    if p.store == "amazon_sa":
        lines.append("⚠️ تأكد أن البائع «أمازون» وأن المنتج جديد قبل الشراء")
    lines.append("")
    lines.append(f'🔗 <a href="{e(p.url, quote=True)}">افتح الصفقة</a>')
    return "\n".join(lines)


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, dry_run: bool = False):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.dry_run = dry_run or not (self.token and self.chat_id)
        if self.dry_run:
            log.warning("وضع تجريبي: لن يُرسل شيء لتلقرام (لا يوجد توكن/معرّف محادثة)")

    def _post(self, method: str, payload: dict) -> bool:
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload, timeout=20)
            if r.status_code != 200:
                log.error("Telegram %s فشل: %s %s", method, r.status_code, r.text[:200])
            return r.status_code == 200
        except requests.RequestException as ex:
            log.error("Telegram خطأ شبكة: %s", ex)
            return False

    def send_text(self, text: str) -> bool:
        if self.dry_run:
            print("-----\n" + text + "\n-----")
            return True
        return self._post("sendMessage", {
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    def send_deal(self, p: Product, cat_label: str, emoji: str) -> bool:
        text = format_message(p, cat_label, emoji)
        if self.dry_run:
            return self.send_text(text)
        if p.image and p.image.startswith("http"):
            ok = self._post("sendPhoto", {
                "chat_id": self.chat_id, "photo": p.image, "caption": text, "parse_mode": "HTML",
            })
            if ok:
                return True
        return self.send_text(text)


SCRAPERS = {"amazon_sa": AmazonSA, "noon": Noon, "jarir": Jarir, "extra": Extra}

# ===== main.py =====

import argparse
import logging
from pathlib import Path
from typing import Dict, List

import yaml


log = logging.getLogger("dealhunter")
ROOT = Path(__file__).resolve().parent


def build_scrapers(cfg: dict) -> Dict[str, BaseScraper]:
    out: Dict[str, BaseScraper] = {}
    for store, enabled in cfg["stores"].items():
        if enabled and store in SCRAPERS:
            out[store] = SCRAPERS[store]()
    for cs in cfg.get("custom_stores") or []:
        if not cs.get("enabled", True):
            continue
        key = cs["key"]
        STORE_NAMES_AR[key] = cs.get("name_ar", key)
        rate, imp = float(cs.get("rate_to_sar", 1.0)), bool(cs.get("imported", False))
        if cs["type"] == "shopify":
            out[key] = ShopifyStore(key, cs["base"], rate, imp)
        elif cs["type"] == "jsonld":
            out[key] = JsonLdStore(key, cs["base"], cs["search_path"], rate, imp)
        elif cs["type"] == "amazon":
            out[key] = AmazonGlobal(key, cs["base"], rate)
        elif cs["type"] == "steam":
            out[key] = SteamSpecials()
        else:
            log.warning("نوع متجر غير معروف: %s", cs["type"])
            continue
        out[key].experimental = bool(cs.get("experimental", True))
    return out


def collect(cfg: dict) -> tuple[List[Product], Dict[str, int]]:
    products: Dict[str, Product] = {}
    per_store: Dict[str, int] = {}
    run_deadline = time.monotonic() + cfg.get("total_time_budget_sec", 840)
    for store, scraper in build_scrapers(cfg).items():
        if time.monotonic() > run_deadline:
            log.warning("انتهت الميزانية الكلية للوقت: تخطي %s", store)
            per_store.setdefault(store, 0)
            continue
        scraper.deadline = min(time.monotonic() + cfg.get("store_time_budget_sec", 240), run_deadline)
        per_store[store] = 0
        empty_streak = 0
        for cat_name, cat in cfg["categories"].items():
            if (cat_name == "games") != isinstance(scraper, SteamSpecials):
                continue
            for q in cat["queries"]:
                if empty_streak >= 3:
                    break
                try:
                    found = scraper.search(q)
                except Exception:
                    log.exception("%s: فشل البحث عن '%s'", store, q)
                    continue
                log.info("%s | %s | '%s' -> %d نتيجة", store, cat_name, q, len(found))
                per_store[store] += len(found)
                empty_streak = 0 if found else empty_streak + 1
                for p in found:
                    products.setdefault(p.key, p)
    return list(products.values()), per_store


def current_season(cfg: dict, today=None) -> str:
    import datetime
    today = str(today or datetime.date.today())
    for sn in cfg.get("seasons", []):
        if str(sn["start"]) <= today <= str(sn["end"]):
            return sn["name"]
    return ""


def run(cfg: dict, state: State, tg: Telegram, only_stores: list[str] | None = None) -> int:
    season = current_season(cfg)
    if only_stores:
        cfg["stores"] = {s: (s in only_stores) for s in cfg["stores"]}

    products, per_store = collect(cfg)

    for store, n in per_store.items():
        exp = any(c.get("key") == store and c.get("experimental", True) for c in cfg.get("custom_stores") or [])
        if n == 0 and not exp and state.should_warn(store):
            tg.send_text(f"⚠️ لم أحصل على أي نتائج من {STORE_NAMES_AR.get(store, store)} — "
                         f"غالباً تم حظر الطلبات أو تغيّر تصميم الموقع. راجع سجل التشغيل في GitHub Actions.")

    deals: List[Product] = []
    classified: List[Product] = []
    for p in products:
        cat = classify(p, cfg["categories"])
        if not cat:
            continue
        p.category = cat
        assign_tier(p, cfg)
        p.season = season
        hist = state.history(p.key)
        score_product(p, cfg, hist)
        classified.append(p)
        state.record(p)
        if is_good_deal(p, cfg) and state.should_send(p, cfg["resend_if_drop_pct"]):
            deals.append(p)

    deals.sort(key=lambda x: (TIER_RANK.get(x.tier, 2), -x.score))
    limit = cfg["max_results_per_run"] + (cfg.get("season_extra_results", 0) if season else 0)
    sent = 0
    for p in deals[:limit]:
        c = cfg["categories"][p.category]
        if tg.send_deal(p, c["label"], c.get("emoji", "🔥")):
            state.mark_sent(p)
            sent += 1
    log.info("تم فحص %d منتج، صفقات مؤهلة %d، أُرسل %d", len(products), len(deals), sent)

    with_disc = [x for x in classified if x.discount_pct > 0]
    ge30 = sum(1 for x in with_disc if x.discount_pct >= 30)
    ge50 = sum(1 for x in with_disc if x.discount_pct >= 50)
    top_disc = sorted(with_disc, key=lambda x: x.discount_pct, reverse=True)[:5]
    log.info("مطابق للفئات %d | منها بخصم معلن %d | خصم ≥30%%: %d | ≥50%%: %d",
             len(classified), len(with_disc), ge30, ge50)
    for x in top_disc:
        log.info("  أعلى خصم: %s | %s | %.0f ر.س | خصم %.0f%% | تقييم %s (%s) | درجة %.0f | %s",
                 x.store, x.title[:50], x.price, x.discount_pct, x.rating, x.reviews, x.score, x.tier)

    if not sent and os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch" and state.should_warn("heartbeat"):
        msg = [f"💤 <b>البوت يعمل</b> — لا توجد صفقة تجتاز الفلتر الآن.",
               f"فحصت {len(products)} منتجاً ({len(classified)} مطابق للفئات)، منها {ge30} بخصم ≥30% و{ge50} بخصم ≥50%."]
        if top_disc:
            msg.append("أعلى خصومات وُجدت:")
            for x in top_disc[:3]:
                msg.append(f"• {html.escape(x.title[:55])} — {x.price:,.0f} ر.س (خصم {x.discount_pct:.0f}%)")
        tg.send_text("\n".join(msg))

    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        lines = ["📊 <b>تقرير التشغيل اليدوي</b>", ""]
        for st, n in per_store.items():
            lines.append(f"• {STORE_NAMES_AR.get(st, st)}: {n} منتج" + ("  ⚠️ لا نتائج (محظور؟)" if n == 0 else ""))
        lines.append(f"✅ منتجات مطابقة للفئات: {len(classified)}")
        lines.append(f"🎯 صفقات مؤهلة: {len(deals)} | أُرسل: {sent}")
        near = sorted((x for x in classified if x.discount_pct > 0), key=lambda x: x.score, reverse=True)[:3]
        if near and not sent:
            lines += ["", "أقرب العروض (لم تبلغ الحد المطلوب):"]
            for x in near:
                lines.append(f"• {html.escape(x.title[:60])} — {x.price:,.0f} ر.س (خصم {x.discount_pct:.0f}%، درجة {x.score:.0f})")
        tg.send_text("\n".join(lines))
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description="صائد صفقات البي سي")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="اطبع الصفقات بدل إرسالها لتلقرام")
    ap.add_argument("--stores", nargs="*", help="تشغيل متاجر محددة فقط: amazon_sa noon jarir")
    ap.add_argument("--test-telegram", action="store_true", help="أرسل رسالة اختبار وانتهِ")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text("utf-8"))
    tg = Telegram(dry_run=args.dry_run)

    if args.test_telegram:
        tg.send_text("✅ البوت يعمل! سأرسل لك أفضل صفقات الكيبورد والماوس وكروت الشاشة هنا.")
        return

    state = State()
    run(cfg, state, tg, args.stores)
    state.save()


if __name__ == "__main__":
    main()
