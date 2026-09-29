import pandas as pd
import streamlit as st

CSV_PATH = r"C:\Users\yigit.dogan\Desktop\FiiliDolasim\endeks_agirlik_ds_genel_20260928.csv"

st.set_page_config(page_title="Fiili Dolaşım Takip", layout="wide")
st.title("Fiili Dolaşım Takip")

TR_MAP = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")


def norm(text: str) -> str:
    """Turkish-safe, uppercase, single-spaced version of a string."""
    return " ".join(str(text).translate(TR_MAP).upper().split())


def to_float(x):
    """'1.234,56' -> 1234.56 ; '12,5' -> 12.5 ; '12.5' -> 12.5"""
    if pd.isna(x):
        return None
    s = str(x).strip().replace("%", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def read_csv_robust(path: str) -> pd.DataFrame:
    last_err = None
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return pd.read_csv(path, sep=";", encoding=enc, dtype=str)
        except UnicodeDecodeError as e:
            last_err = e
    raise last_err


def load_current_ratios(path: str) -> pd.DataFrame:
    df = read_csv_robust(path)
    df.columns = [str(c).strip() for c in df.columns]

    # B kolonu = ENDEKS KODU (pozisyona göre), XUTUM filtresi
    endeks_col = df.columns[1]
    mask = df[endeks_col].astype(str).map(norm) == "XUTUM"
    df = df[mask].copy()

    # FIILI DOLASIMDAKI PAY ORANI kolonunu isimden bul
    ratio_col = next(
        (c for c in df.columns if "FIILI DOLASIMDAKI PAY ORANI" in norm(c)), None
    )
    if ratio_col is None:
        raise ValueError(f"Oran kolonu bulunamadı. Kolonlar: {list(df.columns)}")

    df["MEVCUT_FIILI_DOLASIM"] = df[ratio_col].map(to_float)
    return df.reset_index(drop=True)


run = st.button("Run", type="primary")

if run:
    try:
        cur = load_current_ratios(CSV_PATH)
    except Exception as e:
        st.error(f"CSV okunamadı: {e}")
        st.stop()

    st.success(f"XUTUM filtresi sonrası {len(cur)} satır")
    st.subheader("Mevcut fiili dolaşım oranları (CSV)")
    st.dataframe(cur, use_container_width=True)

    # Adım 2: VAP'tan son 5 günlük veri buraya eklenecek
    # Adım 3: |fark| > 5 bildirimleri ve hisse bazlı detay
