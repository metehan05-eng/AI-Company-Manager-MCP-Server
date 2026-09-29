# Guvenlik Politikasi

## Desteklenen Surumler

| Surum | Destekleniyor |
| ----- | ------------- |
| 0.2.x  | Evet          |
| 0.1.x  | Yalnizca guvenlik duzeltmeleri |
| < 0.1  | Hayir         |

## Guvenlik Acigi Bildirimi

Bir acigi buldugunuzu dusun, lutfen **herkese acik issue acmayin**. Bunun yerine
GitHub Security Advisories uzerinden ozel bildirim gonderin:
https://github.com/metehan05-eng/AI-Company-Manager-MCP-Server/security/advisories/new

Bildiriminizi asagidakileri icermesini rica ederiz:

- Etkilenen surum ve isletim sistemi
- Sorunun aciklamasi ve etkisi
- Yeniden uretebilir adimlar (varsa kod parcasi)
- Duzeltme onerisi (varsa)

Yanit suresi: 7 gun icinde ilk degerlendirme, 30 gun icinde duzeltme veya
reddedilme gerekcesi.

## Guvenlik Sinirlari

Bu proje bilerek su tasarim kararlarini almistir:

- **Yalnizca yerel dosya erisimi.** Sunucu yalnizca `company_data` dizini
  (veya `COMPANY_DATA_DIR` ile verilen dizin) icinde okur ve yazar.
- **Yol guvenligi.** Mutlak yollar, `..` ile dizin atlamasi ve sembolik baglantilar
  reddedilir; yazma isleci atomiktir.
- **Yerel dosya sifrelemesi yok.** `company_data` icindeki dosyalar duz metindir.
  Disk seviyesinde sifreleme kullanin. Depodaki `company_data` icerigi herkese acik
  degildir, cunku `.gitignore` ile dislanir; yine de API anahtari veya sir sirri
  eklemeyin.
- **Formul enjeksiyonu korumasi.** CSV ve XLSX yaziminda hucre degerleri
  `=`, `+`, `-`, `@` ile basliyorsa guvenli hale getirilir.
- **Sifre zorunlu degil.** MCP sunucusu kimlik dogrulama yapmaz; erisim
  istemcinin (Cursor, Claude Desktop) kullanici yetkisine baglidir.
- **Kurulum betigi yazma yapar.** `install.py` yalnizca MCP yapilandirma
  dosyalarini gunceller. Yalnizca kontrol yapmak icin `--check` kullanin.

## Bagimliliklar

Paketleri `pip list --outdated` veya `uv pip list --outdated` ile kontrol edebilirsiniz.
CI, her push'ta guncel surumlerle kurulum yapar.
