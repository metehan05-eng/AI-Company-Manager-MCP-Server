# Katkida Bulunma

AI Company Manager MCP'ye katkida bulundugunuz icin tesekkurler. Bu dosya
gelistirme surecini ve projeye uyum saglayacak temel kurallari anlatir.

## Gelistirme Ortami

```bash
git clone https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server.git
cd AI-Company-Manager-MCP-Server

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

python -m pip install -e ".[dev]"
```

## Dogrulama

Calistirmadan once uc kontrol de yapilmalidir:

```bash
ruff format --check --no-cache src tests install.py
ruff check --no-cache src tests install.py
mypy --python-version 3.10 src install.py
pytest
```

Tek komutta hepsi:

```bash
ruff format --no-cache src tests install.py && ruff check --no-cache src tests install.py && mypy --python-version 3.10 src install.py && pytest
```

## Kurallar

- **Kapsam dar kalsin.** Tek bir is mantigi degisikligi icin acilan PR tercih edilir.
- **Yeni davranis icin test yazin.** Yeni bir arac ekliyorsaniz ayni dosyada hem
  Python seviyesinde hem MCP seviyesinde test olmali.
- **Tur ipuclari zorunlu.** Tum fonksiyon parametreleri ve donus tipleri anotasyonlu
  olmali (`mypy` `disallow_untyped_defs` ile bunu zorunlu kilar).
- **Modul seviyesinde yan etki olmasin.** Dosya sistemi veya ortam degisikligi
  fonksiyon icinde yapilir, import sirasinda degil.
- **Para degerlerinde `Decimal` kullanin.** `float` yalnizca arac sinirlari icin
  ve erken bir `Decimal` donusumuyle birlikte.
- **Yorum satiri eklemeyin.** Kod kendini aciklamalidir; docstring kullanin.
- **Cikti dili.** Kullaniciya donuk mesajlar, hata metinleri ve arac aciklamalari
  Ingilizce ve kisa olmali. Turkce yorumlar ve dokumantasyon kabul edilir.

## Is Akisi

1. Konu acin: neyi degistireceksiniz ve nedenini yazin.
2. `feature/...` veya `fix/...` dalindan calisin.
3. Yukaridaki dort kontrolun tamami gecmeli.
4. `CHANGELOG.md` dosyasini guncelleyin.
5. PR acin ve testlerin gectigini yazma kismina ekleyin.

CI, her push ve PR'da ayni kontrolleri Ubuntu, Windows ve macOS uzerinde calistirir.

## Guvenlik

Guvenlik acigi bildirimleri icin `SECURITY.md` dosyasina bakiniz; herkese acik
issue acmayin.

## Sozlesme (Contract) Davranisi

Arac semalari ve dosya formatlari geriye uyumlu sekilde korunur. Bir aracin
girdi alanlarini degistirmek, onu mevcut istemciler icin kirici bir degisikliktir;
boyle bir degisiklik major surum gerektirir.
