"""
app.py
======
Yargıtay / Danıştay / Emsal karar arama-indirme aracının BULUTTA çalışan,
telefon tarayıcısından kullanılan mobil web arayüzü.

Bu sürüm, kullanıcının kendi Ubuntu makinesinde GERÇEKTEN ÇALIŞTIĞI
doğrulanmış `scraper_core.py` (eski adıyla karar_indir.py) dosyasını
hiç değiştirmeden kullanır. scraper_core.py içindeki fonksiyonlar
ilerlemeyi `print()` ile terminale yazdırdığı için, burada arka plan
thread'i çalışırken stdout geçici olarak yakalanıp hem normal konsola
hem de web arayüzünün ilerleme kutusuna yönlendirilir.

Akış:
1) Telefon tarayıcısı bu sunucuya bağlanır.
2) Kullanıcı kelime/tarih/kaynak/adet seçip "Ara ve İndir" der.
3) Sunucu arka planda (thread) scraper_core fonksiyonlarını çağırır.
4) Telefon, /durum/<job_id> adresini periyodik sorgulayıp ilerlemeyi
   (yakalanan print satırlarını) gösterir.
5) İş bitince PDF'ler tek bir .zip içinde indirilebilir hale gelir.

NOT: İşler sunucunun belleğinde (RAM) tutulur; sunucu yeniden başlarsa
devam eden işler kaybolur — kişisel/küçük ölçekli kullanım için yeterli.
"""

import os
import io
import sys
import uuid
import shutil
import logging
import threading
import contextlib
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file, abort

import scraper_core as sc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("app")

app = Flask(__name__)

TEMEL_KLASOR = os.path.join(os.path.dirname(__file__), "indirilenler")
os.makedirs(TEMEL_KLASOR, exist_ok=True)

# Veritabanını başlat
sc.db_olustur()

KAYNAK_BILGISI = {
    "yargitay": {"ad": "Yargıtay",                 "fonksiyon": sc.yargitay_ara_ve_indir},
    "danistay": {"ad": "Danıştay",                  "fonksiyon": sc.danistay_ara_ve_indir},
    "emsal":    {"ad": "Emsal Karar Arama (UYAP)", "fonksiyon": sc.emsal_ara_ve_indir},
}

VARSAYILAN_MAKS_SONUC = 20
MUTLAK_MAKS_SONUC = 200

# job_id -> { durum, log:[], zip_yolu, baslangic_zamani }
JOBS = {}
JOBS_KILIT = threading.Lock()

MAKS_ES_ZAMANLI_IS = 1  # scraper_core.py'deki paylaşılan kararlar.db için aynı anda 1 iş
_calisma_kilidi = threading.Semaphore(MAKS_ES_ZAMANLI_IS)


def _job_guncelle(job_id, **kwargs):
    with JOBS_KILIT:
        JOBS[job_id].update(kwargs)


def _job_log_ekle(job_id, mesaj):
    with JOBS_KILIT:
        JOBS[job_id]["log"].append(mesaj)
        JOBS[job_id]["log"] = JOBS[job_id]["log"][-300:]


class _CanliYakalayici(io.TextIOBase):
    """print() ile yazılan satırları hem orijinal stdout'a hem de
    job log'una yönlendiren basit bir stdout sarmalayıcı."""

    def __init__(self, job_id, orijinal_stdout):
        self.job_id = job_id
        self.orijinal = orijinal_stdout
        self._tampon = ""

    def write(self, s):
        self.orijinal.write(s)
        self._tampon += s
        while "\n" in self._tampon:
            satir, self._tampon = self._tampon.split("\n", 1)
            satir = satir.strip()
            if satir:
                _job_log_ekle(self.job_id, satir)
        return len(s)

    def flush(self):
        self.orijinal.flush()


def _arka_planda_calistir(job_id, kelime, baslangic, bitis, kaynaklar, indir, maks_sonuc):
    with _calisma_kilidi:
        try:
            _job_guncelle(job_id, durum="calisiyor")
            job_klasor = os.path.join(TEMEL_KLASOR, job_id)
            os.makedirs(job_klasor, exist_ok=True)

            yakalayici = _CanliYakalayici(job_id, sys.stdout)
            with contextlib.redirect_stdout(yakalayici):
                for site_key in kaynaklar:
                    bilgi = KAYNAK_BILGISI.get(site_key)
                    if not bilgi:
                        continue
                    hedef_klasor = os.path.join(job_klasor, site_key)
                    try:
                        bilgi["fonksiyon"](
                            kelime,
                            baslangic,
                            bitis,
                            klasor=hedef_klasor,
                            headless=True,
                            maks_karar=maks_sonuc,
                        )
                    except Exception as e:
                        print(f"[{bilgi['ad']}] HATA: {e}")

            zip_yolu = None
            # job_klasor altında en az bir PDF varsa zip hazırla
            pdf_var = any(
                f.lower().endswith(".pdf")
                for _, _, dosyalar in os.walk(job_klasor)
                for f in dosyalar
            )
            if indir and pdf_var:
                zip_taban = os.path.join(TEMEL_KLASOR, f"{job_id}_kararlar")
                zip_yolu = shutil.make_archive(zip_taban, "zip", job_klasor)
                _job_log_ekle(job_id, "ZIP dosyası hazırlandı, indirebilirsiniz.")
            elif not pdf_var:
                _job_log_ekle(job_id, "Hiç PDF indirilemedi — yukarıdaki günlüğü kontrol edin.")

            _job_guncelle(
                job_id,
                durum="tamam",
                zip_yolu=zip_yolu,
                bitis_zamani=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as e:
            logger.exception("İş hatası")
            _job_log_ekle(job_id, f"HATA: {e}")
            _job_guncelle(job_id, durum="hata")


@app.route("/")
def anasayfa():
    return render_template(
        "index.html",
        kaynaklar=KAYNAK_BILGISI,
        varsayilan_maks_sonuc=VARSAYILAN_MAKS_SONUC,
        mutlak_maks_sonuc=MUTLAK_MAKS_SONUC,
    )


@app.route("/baslat", methods=["POST"])
def baslat():
    veri = request.form
    kelime = (veri.get("kelime") or "").strip()
    if not kelime:
        return jsonify({"hata": "Aranacak kelime boş olamaz."}), 400

    baslangic = (veri.get("baslangic") or "").strip()
    bitis = (veri.get("bitis") or "").strip()
    kaynaklar = veri.getlist("kaynaklar")
    kaynaklar = [k for k in kaynaklar if k in KAYNAK_BILGISI] or list(KAYNAK_BILGISI.keys())

    indir_raw = veri.get("indir", "true")
    indir = str(indir_raw).lower() in ("1", "true", "on", "yes")

    try:
        maks_sonuc = int(veri.get("maks_sonuc", VARSAYILAN_MAKS_SONUC))
    except (TypeError, ValueError):
        maks_sonuc = VARSAYILAN_MAKS_SONUC
    maks_sonuc = max(1, min(maks_sonuc, MUTLAK_MAKS_SONUC))

    job_id = uuid.uuid4().hex[:12]
    with JOBS_KILIT:
        JOBS[job_id] = {
            "durum": "kuyrukta",
            "log": [],
            "zip_yolu": None,
            "kelime": kelime,
            "baslangic_zamani": datetime.now().isoformat(timespec="seconds"),
        }

    thread = threading.Thread(
        target=_arka_planda_calistir,
        args=(job_id, kelime, baslangic, bitis, kaynaklar, indir, maks_sonuc),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/durum/<job_id>")
def durum(job_id):
    with JOBS_KILIT:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"hata": "İş bulunamadı."}), 404

    return jsonify({
        "durum": job["durum"],
        "log": job["log"],
        "zip_hazir": bool(job.get("zip_yolu")),
    })


@app.route("/indir-zip/<job_id>")
def indir_zip(job_id):
    with JOBS_KILIT:
        job = JOBS.get(job_id)
    if not job or not job.get("zip_yolu") or not os.path.exists(job["zip_yolu"]):
        abort(404)
    return send_file(job["zip_yolu"], as_attachment=True,
                      download_name=f"kararlar_{job_id}.zip")


@app.route("/saglik")
def saglik():
    return jsonify({"durum": "ayakta"})


if __name__ == "__main__":
    sc.db_olustur()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
