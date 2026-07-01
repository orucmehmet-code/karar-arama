# Karar Arama — Mobil/Bulut Sürümü (v2 — kanıtlanmış çekirdek)

Bu sürüm, sizin Ubuntu makinenizde **gerçekten çalıştığı doğrulanmış**
`karar_indir.py` dosyasını (`scraper_core.py` adıyla, içeriği değiştirilmeden)
temel alır. Üzerine sadece bir Flask web arayüzü eklenmiştir — Selenium
mantığının kendisine dokunulmamıştır.

## Yapı

```
app.py              -> Flask web sunucusu (mobil arayüz + arka plan işleri)
scraper_core.py      -> SİZİN ÇALIŞAN karar_indir.py dosyanız (Danıştay desteği eklendi)
templates/index.html -> Telefon için mobil arayüz
static/style.css     -> Mobil uyumlu, sade tasarım
Dockerfile            -> Bulutta Chromium + uygulamayı paketler
requirements.txt      -> flask, selenium, gunicorn, pdfplumber
render.yaml           -> Render.com Blueprint yapılandırması
```

## scraper_core.py'de neyi değiştirdim, neyi değiştirmedim

**Değiştirmedim:** Sütun eşlemesi, sayfalama, "içerik stabil olana kadar
bekleme", PDF doğrulama/tekrar deneme, SQLite tekrar-indirme-önleme —
hepsi sizin test ettiğiniz haliyle aynen duruyor.

**Eklediğim:** `danistay_ara_ve_indir()` fonksiyonu — Yargıtay'daki
kanıtlanmış "Detaylı Arama sekmesi dene, olmazsa basit aramaya düş"
mantığının aynısıyla yazıldı, ama **Danıştay sitesinde henüz canlı
test edilmedi**. İlk kullanımda loglara dikkat edin; çalışmazsa
Yargıtay/Emsal'i etkilemeden sadece bu fonksiyonu düzeltiriz.

**Web sarmalayıcısı (app.py):** `print()` çıktılarınızı olduğu gibi
yakalayıp telefon ekranındaki ilerleme kutusuna da yansıtıyor — terminal
çıktınız hiç değişmedi, sadece ek bir "dinleyici" eklendi.

## Yerel test (bilgisayarınızda)

```bash
docker build -t karar-arama .
docker run -p 8080:8080 karar-arama
```
`http://localhost:8080` adresinden deneyin.

## Render.com'a dağıtım

1. Bu klasörü GitHub reponuza **tamamen** gönderin (eski dosyaların hepsinin
   üzerine yazılsın — kısmi/karışık yükleme yapmayın, mümkünse `git push`
   kullanın, web üzerinden tek tek dosya düzenlemek hataya çok açık).
2. Render → **New +** → **Blueprint** → reponuzu seçin → **Apply**.
3. Build tamamlanınca verilen `https://....onrender.com` adresini açın.

## Bilinen sınırlamalar
- Aynı anda yalnızca 1 arama işi çalışır (paylaşılan `kararlar.db`'nin
  bozulmaması için).
- `kararlar.db` ve indirilen PDF'ler konteyner içinde tutulur; Render
  servisi yeniden başlarsa (deploy, restart) bu veriler sıfırlanır —
  kalıcı saklama isterseniz Render'da bir "Disk" eklenmesi gerekir.
- Danıştay desteği yeni eklendi, gerçek sitede henüz doğrulanmadı.
