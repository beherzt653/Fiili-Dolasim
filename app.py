import datetime as dt
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

THRESHOLD = 5.0   # mutlak fark eşiği (puan)
N_DAYS = 5        # kaç gün geriye bakılacak

st.set_page_config(page_title="Fiili Dolaşım Takip", layout="wide")
st.title("Fiili Dolaşım Takip")


# ---------------------------------------------------------------
# 1) VAP'tan tek gün veri çekme
#    Akış (tarayıcıdaki "Tarih Bazında Tüm Şirketler Raporu" ile aynı):
#      GET  /api/all-companies  -> formdaki gizli as_fid alanı
#      POST /api/all-companies  (date=GG/AA/YYYY, as_fid) -> Excel dosyası
# ---------------------------------------------------------------
VAP_URL = "https://www.vap.org.tr/api/all-companies"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
FETCH_LOG: list[str] = []   # her Run'da doldurulur, arayüzde gösterilir


def to_float(x):
    """'71.56' / '71,56' / 71.56 -> 71.56"""
    if pd.isna(x):
        return None
    s = str(x).strip().replace("%", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_report(content: bytes) -> pd.DataFrame | None:
    """Excel içeriğinden KOD / ORAN tablosu çıkarır."""
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=object)
    hdr = next((i for i in range(min(len(raw), 15))
                if "Pay Kodu" in raw.iloc[i].astype(str).tolist()), None)
    if hdr is None:
        return None
    raw.columns = [str(c).strip() for c in raw.iloc[hdr]]
    raw = raw.iloc[hdr + 1:]
    df = pd.DataFrame({"KOD": raw["Pay Kodu"].astype(str).str.strip(),
                       "ORAN": raw["Fiili Dolaşım Oranı"].map(to_float)})
    df = df[(df["KOD"] != "") & (df["KOD"] != "nan")].dropna(subset=["ORAN"])
    return df if not df.empty else None


def fetch_day_vap(date: dt.date) -> pd.DataFrame | None:
    """Tek günün tüm şirket raporunu indirir. Veri yoksa None döner."""
    cache_file = CACHE_DIR / f"{date.isoformat()}.csv"
    if cache_file.exists():                      # geçmiş günler değişmez -> diskten oku
        FETCH_LOG.append(f"{date}: önbellekten okundu")
        return pd.read_csv(cache_file)

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"})
    r = s.get(VAP_URL, timeout=30)
    r.raise_for_status()
    m = re.search(r'name="as_fid"\s+value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("VAP formunda as_fid bulunamadı (site yapısı değişmiş olabilir).")

    r2 = s.post(VAP_URL, data={"date": date.strftime("%d/%m/%Y"), "as_fid": m.group(1)},
                timeout=90)
    if "spreadsheetml" not in r2.headers.get("content-type", ""):
        snippet = re.sub(r"<[^>]+>", " ", r2.text)
        snippet = " ".join(snippet.split())[:200]
        FETCH_LOG.append(f"{date}: Excel gelmedi (HTTP {r2.status_code}) -> {snippet}")
        return None

    df = parse_report(r2.content)
    if df is None:
        FETCH_LOG.append(f"{date}: rapor boş")
        return None
    df.to_csv(cache_file, index=False)
    FETCH_LOG.append(f"{date}: VAP'tan indirildi ({len(df)} şirket)")
    return df


def fetch_day_demo(date: dt.date) -> pd.DataFrame | None:
    """Arayüzü denemek için sahte veri (gerçek veri DEĞİLDİR)."""
    rng = np.random.default_rng(int(date.strftime("%Y%m%d")))
    kodlar = ["AKBNK", "ASELS", "BIMAS", "EREGL", "GARAN", "THYAO", "TUPRS", "YKBNK"]
    base = np.array([48, 25, 70, 40, 45, 50, 20, 35], dtype=float)
    return pd.DataFrame({"KOD": kodlar, "ORAN": base + rng.normal(0, 2.5, len(kodlar))})


# ---------------------------------------------------------------
# 2) Son N iş gününü topla
#    Sitede 10 dk'da en fazla 5 rapor sınırı var -> günler diske (cache/) kaydedilir
# ---------------------------------------------------------------
def cached_fetch(date: dt.date, demo: bool):
    return fetch_day_demo(date) if demo else fetch_day_vap(date)


def collect_last_days(n: int, demo: bool) -> pd.DataFrame:
    """Index=KOD, kolonlar=tarih (eskiden yeniye). Tatil/hafta sonu atlanır."""
    series, d, tries = {}, dt.date.today() - dt.timedelta(days=1), 0
    while len(series) < n and tries < 14:
        if d.weekday() < 5:
            df = cached_fetch(d, demo)
            if df is not None and not df.empty:
                series[d] = df.drop_duplicates("KOD").set_index("KOD")["ORAN"]
        d -= dt.timedelta(days=1)
        tries += 1
    hist = pd.DataFrame(series)
    return hist[sorted(hist.columns)]


# ---------------------------------------------------------------
# 3) Karşılaştırma: önceki günlerin her değeri ile mevcut oran arasındaki |fark|
# ---------------------------------------------------------------
def compare(hist: pd.DataFrame, threshold: float):
    current = hist.iloc[:, -1]              # mevcut = en güncel gün
    past = hist.iloc[:, :-1]                # önceki günler
    diffs = past.sub(current, axis=0)       # geçmiş - mevcut
    max_abs = diffs.abs().max(axis=1)
    out = past.copy()
    out.columns = [c.strftime("%d.%m") for c in past.columns]
    out["MEVCUT"] = current
    for c in past.columns:
        out[f"Δ {c.strftime('%d.%m')}"] = diffs[c]
    out["MAKS |FARK|"] = max_abs
    out["UYARI"] = max_abs > threshold
    return out.sort_values("MAKS |FARK|", ascending=False), current


# ---------------------------------------------------------------
# Arayüz
# ---------------------------------------------------------------
demo = st.checkbox("Demo veri (sahte, sadece arayüzü denemek için)", value=False)
run = st.button("Run", type="primary")

if run:
    FETCH_LOG.clear()
    with st.spinner("Veriler çekiliyor..."):
        try:
            hist = collect_last_days(N_DAYS, demo)
        except NotImplementedError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Veri çekilemedi: {e}")
            st.stop()

    with st.expander("Veri çekme günlüğü"):
        st.write("\n\n".join(FETCH_LOG) or "-")

    if hist.shape[1] < 2:
        st.warning("Karşılaştırma için en az 2 günlük veri gerekli. "
                   "Sitede 10 dakikada en fazla 5 rapor sınırı var; günlüğe bakıp biraz bekleyin.")
        st.stop()

    result, current = compare(hist, THRESHOLD)
    days = ", ".join(c.strftime("%d.%m.%Y") for c in hist.columns)
    st.caption(f"Kullanılan günler: {days} | Mevcut = {hist.columns[-1].strftime('%d.%m.%Y')}")

    # Bildirim ekranı
    st.subheader(f"Bildirimler (|fark| > {THRESHOLD:g})")
    alerts = result[result["UYARI"]]
    if alerts.empty:
        st.success("Eşiği aşan hisse yok.")
    else:
        for kod, row in alerts.iterrows():
            st.warning(f"**{kod}**: mevcut %{row['MEVCUT']:.2f}, "
                       f"önceki günlerdeki en büyük mutlak fark {row['MAKS |FARK|']:.2f} puan")

    # Hisse bazlı detay
    st.subheader("Hisse bazlı son günlerin oranları ve farklar")
    num_cols = [c for c in result.columns if c != "UYARI"]
    st.dataframe(
        result.style.format("{:.2f}", subset=num_cols).apply(
            lambda r: ["background-color:#ffe0e0" if r["UYARI"] else "" for _ in r], axis=1
        ),
        use_container_width=True,
    )
