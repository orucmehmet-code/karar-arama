"""
scraper_core.py  —  v5  (Bedesten API — emsal-karar endpoint'leri)
===================================================================
Yargıtay / Danıştay / Emsal karar arama + PDF/HTML indirme.

Gerçek çalışan endpoint'ler:

  Arama : POST https://bedesten.adalet.gov.tr/emsal-karar/searchDocuments
          body: {"data": {"pageSize":10,"pageNumber":1,
                           "itemTypeList":["YARGITAYKARARI"],"phrase":"..."}}

  Belge : POST https://bedesten.adalet.gov.tr/emsal-karar/getDocumentContent
          body: {"data": {"documentId":"..."}}
          → içerik base64 encoded döner; HTML ya da doğrudan PDF olabilir.
"""

import os
import re
import time
import gc
import base64
import sqlite3
import logging
from pathlib import Path

import requests

try:
    import pdfkit
    PDFKIT_MEVCUT = True
except ImportError:
    PDFKIT_MEVCUT = False

logger = logging.getLogger("scraper_core")

# ── Bedesten API sabitler ────────────────────────────────────────────────────
BEDESTEN_BASE    = "https://bedesten.adalet.gov.tr"
SEARCH_PATH      = "/emsal-karar/searchDocuments"
CONTENT_PATH     = "/emsal-karar/getDocumentContent"

# ── Veritabanı ──────────────────────────────────────────────────────────────
DB_YOLU = os.path.join(os.path.dirname(__file__), "kararlar.db")

def db_olustur():
    con = sqlite3.connect(DB_YOLU)
    con.execute("""
        CREATE TABLE IF NOT EXISTS kararlar (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mahkeme   TEXT,
            daire     TEXT,
            esas_no   TEXT,
            karar_no  TEXT,
            tarih     TEXT,
            dosya     TEXT,
            indirildi INTEGER DEFAULT 0
        )
    """)
    mevcut = [r[1] for r in con.execute("PRAGMA table_info(kararlar)").fetchall()]
    if "dosya" not in mevcut:
        con.execute("ALTER TABLE kararlar ADD COLUMN dosya TEXT")
    con.commit()
    con.close()

def zaten_indirildi_mi(mahkeme, esas, karar):
    con = sqlite3.connect(DB_YOLU)
    cur = con.execute(
        "SELECT 1 FROM kararlar WHERE mahkeme=? AND esas_no=? AND karar_no=? AND indirildi=1",
        (mahkeme, esas, karar)
    )
    sonuc = cur.fetchone() is not None
    con.close()
    return sonuc

def db_kaydet(mahkeme, daire, esas, karar, tarih, dosya):
    con = sqlite3.connect(DB_YOLU)
    con.execute(
        "INSERT OR REPLACE INTO kararlar "
        "(mahkeme,daire,esas_no,karar_no,tarih,dosya,indirildi) VALUES (?,?,?,?,?,?,1)",
        (mahkeme, daire, esas, karar, tarih, dosya)
    )
    con.commit()
    con.close()

def rapor_yazdir():
    con = sqlite3.connect(DB_YOLU)
    print("\n" + "="*55)
    print("  VERİTABANI RAPORU")
    print("="*55)
    cur = con.execute(
        "SELECT mahkeme, COUNT(*), MIN(tarih), MAX(tarih) "
        "FROM kararlar WHERE indirildi=1 GROUP BY mahkeme"
    )
    toplam = 0
    for mahkeme, adet, ilk, son in cur.fetchall():
        print(f"  {mahkeme:<12} → {adet} karar  ({ilk} — {son})")
        toplam += adet
    print(f"\n  TOPLAM: {toplam} karar")
    print("="*55)
    con.close()

# ── Yardımcılar ─────────────────────────────────────────────────────────────

def dosya_adi_temizle(metin, uzunluk=60):
    metin = (metin or "karar").strip()
    for ch in r'\/:*?"<>|':
        metin = metin.replace(ch, "_")
    metin = re.sub(r"\s+", "_", metin)
    return metin[:uzunluk] if metin else "karar"

def html_to_pdf(html_icerik, dosya_yolu):
    """HTML içeriğini PDF'e ya da HTML dosyasına kaydeder."""
    if not PDFKIT_MEVCUT:
        html_yolu = dosya_yolu.replace(".pdf", ".html")
        with open(html_yolu, "w", encoding="utf-8") as f:
            f.write(html_icerik)
        print(f"  [BİLGİ] pdfkit yok → HTML: {os.path.basename(html_yolu)}")
        return html_yolu
    try:
        secenekler = {"encoding": "UTF-8", "quiet": ""}
        pdfkit.from_string(html_icerik, dosya_yolu, options=secenekler)
        return dosya_yolu
    except Exception as e:
        print(f"  [UYARI] PDF dönüşümü başarısız ({e}) → HTML kaydediliyor")
        html_yolu = dosya_yolu.replace(".pdf", ".html")
        with open(html_yolu, "w", encoding="utf-8") as f:
            f.write(html_icerik)
        return html_yolu

def icerik_kaydet(ham_bayt, dosya_yolu):
    """
    getDocumentContent'ten base64 çözülmüş ham içeriği kaydeder.
    İçerik doğrudan PDF olabilir (magic bytes: %PDF) ya da HTML/metin olabilir.
    """
    if ham_bayt[:4] == b"%PDF":
        with open(dosya_yolu, "wb") as f:
            f.write(ham_bayt)
        return dosya_yolu

    # PDF değilse HTML/metin olarak kabul et
    try:
        metin = ham_bayt.decode("utf-8")
    except UnicodeDecodeError:
        metin = ham_bayt.decode("utf-8", errors="replace")

    return html_to_pdf(metin, dosya_yolu)

def guvenli_istek(ses, method, url, bekleme_ilk=3, **kwargs):
    """429 / geçici hatalarda exponential backoff ile yeniden dener."""
    bekleme = bekleme_ilk
    for deneme in range(5):
        try:
            r = ses.request(method, url, **kwargs)
            if r.status_code == 429:
                print(f"  [429] Çok hızlı — {bekleme}s bekleniyor (deneme {deneme+1}/5)")
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 60)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                print(f"  [429] Çok hızlı — {bekleme}s bekleniyor (deneme {deneme+1}/5)")
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 60)
                continue
            raise
    raise Exception(f"5 denemeden sonra başarısız: {url}")

def _ilk_liste_bul(obj):
    """
    Arama yanıtındaki karar listesini esnek biçimde bulur.
    Beklenen: veri["data"] içinde bir liste alan (örn. "emsalKararList",
    "data", "content", "results" vb.) — anahtar adı garanti olmadığından
    dict içindeki ilk liste-of-dict alanını arar.
    """
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    # Önce sık kullanılan olası anahtarları dene
    for anahtar in ("emsalKararList", "data", "content", "results",
                     "decisions", "documents", "items", "list"):
        deger = obj.get(anahtar)
        if isinstance(deger, list):
            return deger
        if isinstance(deger, dict):
            iceride = _ilk_liste_bul(deger)
            if iceride:
                return iceride
    # Hiçbiri tutmadıysa tüm değerleri tara
    for deger in obj.values():
        if isinstance(deger, list) and deger and isinstance(deger[0], dict):
            return deger
        if isinstance(deger, dict):
            iceride = _ilk_liste_bul(deger)
            if iceride:
                return iceride
    return []

def _toplam_kayit_bul(obj, varsayilan=0):
    if not isinstance(obj, dict):
        return varsayilan
    for anahtar in ("total", "totalElementCount", "totalElements",
                     "totalRecords", "totalCount", "recordCount"):
        if anahtar in obj and isinstance(obj[anahtar], int):
            return obj[anahtar]
        veri = obj.get("data")
        if isinstance(veri, dict) and anahtar in veri and isinstance(veri[anahtar], int):
            return veri[anahtar]
    return varsayilan

def _b64_icerik_bul(obj):
    """getDocumentContent yanıtından base64 alanını esnek biçimde bulur."""
    if not isinstance(obj, dict):
        return None
    veri = obj.get("data", obj)
    if isinstance(veri, dict):
        for anahtar in ("content", "documentContent", "base64Content",
                         "data", "fileContent", "contentBase64"):
            deger = veri.get(anahtar)
            if isinstance(deger, str) and len(deger) > 50:
                return deger
    # son çare: dict içindeki en uzun string alanı al
    if isinstance(veri, dict):
        adaylar = [v for v in veri.values() if isinstance(v, str)]
        if adaylar:
            return max(adaylar, key=len)
    return None

# ── Bedesten API arama + indirme ─────────────────────────────────────────────

def bedesten_ara_ve_indir(
    kelime,
    mahkeme_adi,
    on_ek,
    item_type_list,       # ["YARGITAYKARARI"] veya ["DANISTAYKARAR"]
    baslangic="",
    bitis="",
    klasor="kararlar",
    maks_karar=None,
):
    os.makedirs(klasor, exist_ok=True)
    print(f"\n[{mahkeme_adi.upper()}] Aranıyor: {kelime}")

    ses = requests.Session()
    ses.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":       "application/json",
        "Content-Type": "application/json",
        "Origin":       BEDESTEN_BASE,
        "Referer":      BEDESTEN_BASE + "/",
    })
    # Ana sayfayı aç (çerez/session için)
    try:
        ses.get(BEDESTEN_BASE, timeout=15)
    except Exception:
        pass

    sayfa = 1
    sayfa_boyutu = 10
    toplam_indirilen = 0

    while True:
        govde_data = {
            "pageSize":     sayfa_boyutu,
            "pageNumber":   sayfa,
            "itemTypeList": item_type_list,
            "phrase":       kelime,
        }
        if baslangic:
            govde_data["kararTarihiStart"] = baslangic
        if bitis:
            govde_data["kararTarihiEnd"] = bitis

        govde = {"data": govde_data}

        try:
            r = guvenli_istek(
                ses, "POST",
                BEDESTEN_BASE + SEARCH_PATH,
                json=govde,
                timeout=30,
            )
            veri = r.json()
        except Exception as e:
            print(f"  [HATA] Arama isteği başarısız: {e}")
            break

        kararlar = _ilk_liste_bul(veri)
        toplam   = _toplam_kayit_bul(veri)

        if not kararlar:
            print(f"  Sonuç bulunamadı. Ham yanıt: {str(veri)[:300]}")
            break

        if sayfa == 1:
            print(f"  Toplam {toplam or len(kararlar)} karar bulundu.")

        for kayit in kararlar:
            if maks_karar and toplam_indirilen >= maks_karar:
                print(f"  [LİMİT] {maks_karar} karar indirildi, durduruluyor.")
                rapor_yazdir()
                return

            doc_id  = str(kayit.get("documentId", "")).strip()
            daire   = str(kayit.get("birimAdi", "")).strip()
            esas    = str(kayit.get("esasNo", "")).strip()
            karar   = str(kayit.get("kararNo", "")).strip()
            tarih   = str(kayit.get("kararTarihiStr", kayit.get("kararTarihi", ""))).strip()

            if not doc_id:
                print("  [ATLA] documentId yok")
                continue

            if not esas:
                esas = f"karar_{sayfa}_{toplam_indirilen+1}_{int(time.time()*1000)}"

            if zaten_indirildi_mi(mahkeme_adi, esas, karar):
                print(f"  [ATLA] Zaten indirildi: {esas}")
                continue

            print(f"  [{toplam_indirilen+1}] {daire} | {esas} | {karar} | {tarih}")

            # ── Belge içeriğini al (base64) ──
            try:
                dr = guvenli_istek(
                    ses, "POST",
                    BEDESTEN_BASE + CONTENT_PATH,
                    json={"data": {"documentId": doc_id}},
                    timeout=30,
                )
                belge_yaniti = dr.json()
            except Exception as e:
                print(f"  [HATA] İçerik alınamadı: {e}")
                time.sleep(2)
                continue

            b64_metin = _b64_icerik_bul(belge_yaniti)
            if not b64_metin:
                print(f"  [ATLA] Belge içeriği bulunamadı. Ham yanıt: {str(belge_yaniti)[:200]}")
                continue

            try:
                ham_bayt = base64.b64decode(b64_metin)
            except Exception as e:
                print(f"  [HATA] Base64 çözülemedi: {e}")
                continue

            if not ham_bayt or len(ham_bayt) < 50:
                print("  [ATLA] Boş/çok kısa içerik")
                continue

            dosya_yolu = os.path.join(
                klasor,
                f"{on_ek}_{dosya_adi_temizle(esas)}_{dosya_adi_temizle(karar)}.pdf"
            )
            gercek_dosya = icerik_kaydet(ham_bayt, dosya_yolu)
            db_kaydet(mahkeme_adi, daire, esas, karar, tarih, gercek_dosya)
            toplam_indirilen += 1
            print(f"     → Kaydedildi: {os.path.basename(gercek_dosya)}")

            if toplam_indirilen % 5 == 0:
                gc.collect()

            time.sleep(1.5)  # sunucuya saygı

        # Sonraki sayfa var mı?
        if toplam:
            toplam_sayfa = (toplam + sayfa_boyutu - 1) // sayfa_boyutu
            sayfa_bitti = sayfa >= toplam_sayfa
        else:
            # toplam bilinmiyorsa: dönen kayıt sayısı sayfa boyutundan azsa bitmiştir
            sayfa_bitti = len(kararlar) < sayfa_boyutu

        if sayfa_bitti or not kararlar:
            print(f"  Sayfalama bitti. Toplam {toplam_indirilen} karar indirildi.")
            break
        sayfa += 1

    rapor_yazdir()


# ── Site fonksiyonları ───────────────────────────────────────────────────────

def yargitay_ara_ve_indir(kelime, baslangic="", bitis="",
                           klasor="kararlar/yargitay", headless=True, maks_karar=None):
    bedesten_ara_ve_indir(
        kelime=kelime, baslangic=baslangic, bitis=bitis,
        klasor=klasor, maks_karar=maks_karar,
        mahkeme_adi="Yargitay", on_ek="yargitay",
        item_type_list=["YARGITAYKARARI"],
    )


def danistay_ara_ve_indir(kelime, baslangic="", bitis="",
                           klasor="kararlar/danistay", headless=True, maks_karar=None):
    bedesten_ara_ve_indir(
        kelime=kelime, baslangic=baslangic, bitis=bitis,
        klasor=klasor, maks_karar=maks_karar,
        mahkeme_adi="Danistay", on_ek="danistay",
        item_type_list=["DANISTAYKARAR"],
    )


def emsal_ara_ve_indir(kelime, baslangic="", bitis="",
                        klasor="kararlar/emsal", headless=True, maks_karar=None):
    """
    Emsal aramasını Yargıtay + Danıştay tür kodlarıyla birlikte,
    aynı emsal-karar endpoint'i üzerinden yapar.
    """
    bedesten_ara_ve_indir(
        kelime=kelime, baslangic=baslangic, bitis=bitis,
        klasor=klasor, maks_karar=maks_karar,
        mahkeme_adi="Emsal", on_ek="emsal",
        item_type_list=["YARGITAYKARARI", "DANISTAYKARAR"],
    )


# ── Komut satırı arayüzü ────────────────────────────────────────────────────

if __name__ == "__main__":
    db_olustur()
    print("=" * 55)
    print("   YARGITAY / DANIŞTAY / EMSAL KARAR İNDİRİCİ")
    print("=" * 55)
    print("1) Yargıtay")
    print("2) Danıştay")
    print("3) Emsal (Yargıtay+Danıştay birlikte)")
    print("4) Hepsi")
    print("5) Veritabanı raporu")
    secim = input("Seçim (1/2/3/4/5): ").strip()

    if secim == "5":
        rapor_yazdir()
    else:
        kelime    = input("Aranacak kelime: ").strip()
        baslangic = input("Başlangıç tarihi (GG.AA.YYYY) [boş=yok]: ").strip()
        bitis     = input("Bitiş tarihi    (GG.AA.YYYY) [boş=yok]: ").strip()

        _maks = input("Kaç karar indirilsin? [boş=tümü]: ").strip()
        maks_karar = int(_maks) if _maks.isdigit() and int(_maks) > 0 else None
        if maks_karar:
            print(f"  → En fazla {maks_karar} karar indirilecek.")

        if secim in ("1", "4"):
            yargitay_ara_ve_indir(kelime, baslangic, bitis, maks_karar=maks_karar)
        if secim in ("2", "4"):
            danistay_ara_ve_indir(kelime, baslangic, bitis, maks_karar=maks_karar)
        if secim in ("3", "4"):
            emsal_ara_ve_indir(kelime, baslangic, bitis, maks_karar=maks_karar)

        rapor_yazdir()
        print("\nTamamlandı! 'kararlar/' klasörünü kontrol edin.")
