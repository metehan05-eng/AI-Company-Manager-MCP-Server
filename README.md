# AI Company Manager MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.30%2B-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Platformlar](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#hızlı-kurulum)
[![Taşıma](https://img.shields.io/badge/transport-stdio%20JSON--RPC-5C4EE5.svg)](#mcp-istemci-yapılandırması)

Yapay zekâ destekli bir MCP (Model Context Protocol) sunucusudur. Cursor, Claude Desktop ve
OpenCode gibi MCP istemcilerine şirket yönetimi araçları sunar: şirket profili, finans kayıtları,
personel listesi ve şirket notları **tamamen yerel dosyalarda** tutulur. Veri buluta çıkmaz, API
anahtarı gerekmez, ücretsizdir.

Tek komutla kurulur, üç platformda (Windows, Linux, macOS) çalışır.

---

## İçindekiler

- [Ne İşe Yarar](#ne-işe-yarar)
- [Özellikler](#özellikler)
- [Gereksinimler](#gereksinimler)
- [Hızlı Kurulum](#hızlı-kurulum)
- [Kurulum Betiğinin Yaptıkları](#kurulum-betiğinin-yaptıkları)
- [Kurulum Seçenekleri](#kurulum-seçenekleri)
- [Elle Kurulum](#elle-kurulum)
- [MCP İstemci Yapılandırması](#mcp-istemci-yapılandırması)
- [MCP Araçları](#mcp-araçları)
- [Örnek İstemci İstekleri](#örnek-istemci-istekleri)
- [Veri Düzeni](#veri-düzeni)
- [PDF ve DOCX Okuma Davranışı](#pdf-ve-docx-okuma-davranışı)
- [Yapılandırma](#yapılandırma)
- [Güvenlik ve Dayanıklılık](#güvenlik-ve-dayanıklılık)
- [Proje Yapısı](#proje-yapısı)
- [Geliştirme](#geliştirme)
- [Yol Haritası](#yol-haritası)
- [Katkı](#katkı)
- [Lisans](#lisans)

---

## Ne İşe Yarar

Bir MCP istemcisine "şirketimizin nakit akışı ne?", "yeni bir çalışan ekle", "geçen toplantının
notlarını yaz" dediğinizde sunucu:

1. İstekleri doğrulanan bir MCP aracı olarak sunar.
2. `company_data/` dizinindeki yerel dosyaları okur veya atomik olarak günceller.
3. Sonucu istemciye metin olarak döndürür.

Böylece yapay zekâ asistanı, şirket verisine **izinsiz erişmeden** ve bulut servisi kullanmadan
çalışır. Tüm değişiklikler düz metin dosyalarına yazıldığı için verileri istediğiniz an
yedekleyebilir, gözden geçirebilir veya elle düzenleyebilirsiniz.

## Özellikler

- **Sıfırdan şirket kurulumu:** profil, finans dosyası, kurucu çalışan kaydı ve standart departman şablonu
- **Finans takibi:** gelir/gider ekleme, otomatik nakit akışı özeti, mevcut bakiye hesabı
- **Personel yönetimi:** doğrulanan çalışan kayıtları ve otomatik `EMP-0001` biçiminde personel numarası
- **Şirket notları:** başlığa göre politika, toplantı, strateji veya vizyon notu oluşturma/güncelleme
- **Çoklu dosya formatı:** TXT, MD, JSON, CSV ve XLSX okuma/yazma; **PDF ve DOCX okuma**
- **Atomik dosya yazımı** ve katı yol sınırlama (sembolik bağlantı ve `..` reddi)
- **Formül enjeksiyonu koruması:** CSV ve XLSX hücrelerinde `=`, `+`, `-`, `@` önekleri etkisizleştirilir
- **Şifreleme/hesap gerektirmez:** hiçbir dış servise bağlanmaz
- **Tek komutlu kurulum:** Windows, Linux ve macOS
- **MCP `stdio` taşıması** (JSON-RPC 2.0)

## Gereksinimler

- Python 3.10 veya üzeri
- İnternet erişimi (yalnızca ilk kurulumda bağımlılık indirmek için)
- `uv` kuruluysa kurulum onunla çok hızlıdır; `uv` yoksa standart `venv` + `pip` yolu kullanılır.
  Ek paket kurmanız gerekmez.

Bağımlılıklar `requirements.txt` içinde sabitlenmiştir:

```text
mcp[cli]>=1.30.0,<2.0.0
pydantic>=2.11.0,<3.0.0
pandas>=2.2.0,<3.0.0
openpyxl>=3.1.5,<4.0.0
pypdf>=3.0.0
python-docx>=0.8.11
```

> **Neden `<2`?** Proje `FastMCP` API'sini kullanır ve MCP Python SDK'nın bakım sürümü olan
> `>=1.30,<2` aralığına sabitlenmiştir.

## Hızlı Kurulum

Projeyi kopyalayıp klasörüne girin ve tek komutu çalıştırın.

**Linux / macOS**

```bash
cd ai-company-manager-mcp
python3 install.py
```

**Windows (PowerShell veya Komut İstemi)**

```powershell
cd ai-company-manager-mcp
python install.py
```

Kurulumdan sonra Cursor'da MCP sunucusunu bir kez kapat/aç. İlk kurulumda paket indirildiği için
birkaç saniye sürebilir; sonraki çalıştırmalar anlıktır.

## Kurulum Betiğinin Yaptıkları

`install.py` üç platformda da aynı şekilde çalışır ve sırasıyla:

1. **Sanal ortamı kurar.** Proje içinde `.venv` oluşturur ve `requirements.txt` bağımlılıklarını
   yükler. `uv` varsa onu kullanır (çok hızlı), yoksa `venv` + `pip` yoluna düşer.
2. **MCP yapılandırmasını yazar.** `<proje>/.cursor/mcp.json` ile `~/.cursor/mcp.json` içine
   `ai-company-manager` sunucusunu ekler. Claude Desktop yapılandırması varsa oraya da ekler.
   Var olan sunucular ve ayarlar korunur; geçersiz dosyalar `.bak` olarak yedeklenir.
3. **Kurulumu doğrular.** Sunucuyu gerçekten başlatır, `initialize` ve `tools/list` istekleri
   gönderir ve kaç aracın listelendiğini yazar. Böylece "bağlanmıyor" hatasını kurulum anında görürsünüz.

Tipik çıktı:

```text
AI Company Manager MCP kurulumu (linux)
uv bulundu; hizli kurulum kullanilacak.
  sanal ortam olusturuluyor
  bagimliliklar kuruluyor
MCP yapilandirmasi yaziliyor...
  guncellendi: .../.cursor/mcp.json
  guncellendi: ~/.cursor/mcp.json
Sunucu dogrulaniyor...
  sunucu: AI Company Manager 1.30.0
  arac sayisi: 7
Bitti (13.5 saniye).
```

## Kurulum Seçenekleri

| Seçenek | İşlev |
|---|---|
| `--force` | Sanal ortamı silip baştan oluşturur. |
| `--skip-deps` | Yalnızca MCP yapılandırmasını yazar (bağımlılık kurmaz). |
| `--no-global` | `~/.cursor` ve Claude Desktop yapılandırmasına dokunmaz. |
| `--skip-verify` | Kurulum sonrası canlı sunucu doğrulamasını atlar. |

Örnekler:

```bash
python3 install.py --force        # bozuk sanal ortamı yeniden kur
python3 install.py --no-global    # yalnızca proje yapılandırmasını yaz
```

## Elle Kurulum

Kurulum betiğini kullanmak istemezseniz:

```bash
cd ai-company-manager-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell için sanal ortam etkinleştirme:

```powershell
.\.venv\Scripts\Activate.ps1
```

MCP Inspector ile elle test etmek için:

```bash
mcp dev src/server.py
```

> `mcp dev` için Node.js ve `npx` gerekir. Sunucu `stdio` üzerinden konuşur; terminale bilgi yazdırmaz.

## MCP İstemci Yapılandırması

`python install.py` çalıştırıldığında Cursor ve Claude Desktop yapılandırmaları otomatik yazılır.
Aşağıdaki dosyaları elle yönetmek isterseniz kullanabilirsiniz.

### Cursor

Cursor, açtığınız klasörün altındaki `.cursor/mcp.json` dosyasını okur. `${workspaceFolder}`
değişkeni açılan klasöre çözülür, böylece aynı dosya her bilgisayarda aynı kalır.

Linux ve macOS için kurulum betiğinin yazdığı içerik:

```json
{
  "mcpServers": {
    "ai-company-manager": {
      "command": "bash",
      "args": ["${workspaceFolder}/run_mcp.sh"],
      "env": {
        "COMPANY_DATA_DIR": "${workspaceFolder}/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

Windows'ta `command` değeri `cmd`, `args` ise `["/c", "${workspaceFolder}\\run_mcp.cmd"]` olur.

Klasörü açmadan her projede kullanmak için `~/.cursor/mcp.json` içindeki mutlak yollu girişi
tercih edebilirsiniz; kurulum betiği her iki dosyayı da günceller.

Başlatıcı betikleri (`run_mcp.sh`, `run_mcp.cmd`) sanal ortam yoksa kendisi kurar ve **tüm kurulum
çıktısını `stderr`'e yazar**; `stdout` yalnızca MCP protokolüne ayrıdır.

### Claude Desktop

Claude Desktop genellikle MCP komutunu uygulamanın çalışma dizininden başlatır. Kurulum betiği bu
nedenle Claude Desktop yapılandırmasına mutlak yollu girişi yazar. Elle yazacaksanız:

macOS/Linux:

```json
{
  "mcpServers": {
    "ai-company-manager": {
      "command": "/ABSOLUT/PATH/ai-company-manager-mcp/.venv/bin/python",
      "args": ["/ABSOLUT/PATH/ai-company-manager-mcp/src/server.py"],
      "env": {
        "COMPANY_DATA_DIR": "/ABSOLUT/PATH/ai-company-manager-mcp/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

Windows'ta `command` değeri `.venv\\Scripts\\python.exe` olmalıdır. Değişiklikten sonra Claude
Desktop yeniden başlatılmalıdır.

### OpenCode

Proje kökünde `opencode.json` oluşturun:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ai-company-manager": {
      "type": "local",
      "command": [
        "/ABSOLUT/PATH/ai-company-manager-mcp/.venv/bin/python",
        "/ABSOLUT/PATH/ai-company-manager-mcp/src/server.py"
      ],
      "enabled": true,
      "environment": {
        "COMPANY_DATA_DIR": "/ABSOLUT/PATH/ai-company-manager-mcp/company_data",
        "COMPANY_MAX_FILE_MB": "10"
      }
    }
  }
}
```

OpenCode ayarı yalnızca başlangıçta okunur; değişiklikten sonra yeniden başlatın.

## MCP Araçları

| Araç | Görev | Zorunlu alanlar |
|---|---|---|
| `list_company_files` | Veri dizinindeki tüm dosyaları tür, boyut ve değişiklik zamanıyla listeler. | — |
| `get_company_overview` | Profil, bütçe, gelir, gider, net nakit akışı ve mevcut bakiye özetini döndürür. | — |
| `read_company_file` | Desteklenen dosyayı AI'ın analiz edebileceği metne dönüştürür. | `filename` |
| `create_new_company` | Profil, finans dosyası ve kurucu satırını oluşturur. | `company_name`, `sector`, `initial_budget` |
| `add_financial_record` | `income` veya `expense` kaydını `financials.json` dosyasına ekler. | `type`, `category`, `amount`, `description` |
| `add_employee` | Yeni personeli `employees.csv` dosyasına ekler. | `name`, `role`, `department`, `salary` |
| `update_company_notes` | Başlığa göre politika, toplantı, strateji veya vizyon notu oluşturur/günceller. | `note_title`, `content` |

Alan sınırları Pydantic ile doğrulanır: boş metin, negatif bütçe, sıfır tutarlı finans kaydı ve
2.000 karakteri aşan açıklama reddedilir.

## Örnek İstemci İstekleri

```text
create_new_company(company_name="Atlas Yazılım", sector="SaaS", initial_budget=2500000)
add_financial_record(type="income", category="services", amount=125000, description="Aylık kurumsal abonelik")
add_employee(name="Deniz Kaya", role="Kıdemli Geliştirici", department="Bilgi Teknolojileri", salary=95000)
read_company_file(filename="employees.csv")
```

Doğal dilde örnekler:

- "Şirketimizin bu ayki nakit akışını özetle."
- "Finanslara 45.000 TL'lik sunucu gideri ekle, kategorisi altyapı olsun."
- "Strateji başlıklı bir not oluştur: 2026'da Avrupa'ya açılmayı hedefliyoruz."
- "Yönetim raporunu oku ve riskleri çıkar."

## Veri Düzeni

Depo, gerçek şirket verisi içermez: `company_data/` klasörü boş gelir ve `.gitignore` tarafından
yok sayılır. Çekirdek dosyalar `create_new_company` aracı ile oluşturulur. Araç, kayıtlı finans
hareketi veya `employees.csv` bulunan bir şirketi tespit ederse yeni şirket kurmayı reddeder; bu,
veri kaybını önler. Başka bir şirket kurmadan önce çekirdek dosyaları yedekleyin.

| Dosya | İçerik |
|---|---|
| `company_data/company_profile.json` | Şirket adı, sektör, kuruluş tarihi, departmanlar, vizyon ve misyon |
| `company_data/financials.json` | Bütçe, kategoriler, işlemler ve nakit akışı şablonu |
| `company_data/employees.csv` | Kurucu ve ek personel kayıtları (`EMP-0001`, `EMP-0002`, ...) |
| `company_data/company_notes.json` | Şirket notları (ilk `update_company_notes` çağrısında oluşur) |
| `company_data/*.pdf`, `*.docx` | Kullanıcının eklediği belgeler (salt okunur) |

Kullanıcılar `company_data/` altına kendi TXT, MD, JSON, CSV, XLSX, PDF veya DOCX belgelerini
ekleyebilir; `read_company_file` bunları okur.

## PDF ve DOCX Okuma Davranışı

| Durum | Davranış |
|---|---|
| Metin içeren PDF | Her sayfa `--- Sayfa N ---` başlığıyla çıkarılır. |
| Metin içermeyen PDF | Sayfa için uyarı döner; belgenin taranmış görsel olduğu belirtilir ve OCR önerilir. |
| Şifreli PDF | Boş parola denenir, başarısızsa anlaşılır bir hata döner. |
| Bozuk PDF/DOCX | Dosya adı ve teknik ayrıntıyı içeren `CompanyFileError` döner. |
| DOCX | Tüm paragraflar ve tüm tablolar (`--- Tablo N ---` başlığıyla, satırlar `\|` ile ayrılmış) çıkarılır. |
| PDF/DOCX yazma | Reddedilir: bu formatlar salt okunurdur. |
| Eksik bağımlılık | Yükleme komutunu söyleyen anlaşılır bir hata döner. |

## Yapılandırma

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `COMPANY_DATA_DIR` | Proje içindeki `company_data` | Mutlak veya proje köküne göreli veri dizini. |
| `COMPANY_MAX_FILE_MB` | `10` | Okuma ve yazma için dosya boyutu sınırı (en az 1). |

`.env.example` yalnızca bir şablondur; ortam değişkenlerini istemci `env` alanından veya işletim
sistemi üzerinden verin.

## Güvenlik ve Dayanıklılık

- Mutlak yollar, `..` dizin geçişleri ve sembolik bağlantılar reddedilir.
- Tüm okuma ve yazma işlemleri veri dizini sınırı içinde kalır.
- Dosyalar aynı dizindeki geçici dosya ve `os.replace` ile atomik olarak güncellenir.
- Finansal ve personel değişiklikleri aynı sihirbaz kilidi altında yapılır.
- JSON, sayısal alanlar, tarihler ve tablo sütunları doğrulanır.
- CSV ve XLSX hücrelerinde formül enjeksiyonu önek karakter ile etkisizleştirilir.
- Kurulum betiği mevcut MCP yapılandırmalarını üzerine yazmaz, birleştirir.
- Veriler düz metindir; API anahtarı veya kimlik doğrulama içermez. Düzenli yedek alın.

## Proje Yapısı

```text
ai-company-manager-mcp/
├── install.py               # Tek komutlu kurulum (Windows/Linux/macOS)
├── run_mcp.sh               # Linux/macOS başlatıcısı (sanal ortamı kendisi kurar)
├── run_mcp.cmd              # Windows başlatıcısı
├── requirements.txt         # Sabitlenmiş bağımlılıklar
├── mcp.json                 # Cursor biçiminde örnek yapılandırma
├── LICENSE                  # MIT
├── .env.example             # Ortam değişkeni şablonu
├── .cursor/mcp.json         # Cursor proje yapılandırması
├── company_data/            # Şirket verileri (yalnızca burada)
└── src/
    ├── server.py            # MCP araç tanımları (FastMCP)
    ├── file_handler.py      # Dosya okuma/yazma, yol sınırlama, PDF/DOCX
    └── company_wizard.py    # İş kuralları, şirket kurulumu, notlar
```

## Geliştirme

Kod stili ve tip denetimi:

```bash
ruff check src install.py
ruff format --check src install.py
mypy --python-version 3.10 --ignore-missing-imports src install.py
```

Değişiklikten sonra canlı doğrulama:

```bash
python3 install.py
```

Bu komut yapılandırmayı yazar, sunucuyu başlatır ve `tools/list` çağrısıyla araç listesini doğrular.
Yalnızca yapılandırmayı yeniden yazmak isterseniz `python3 install.py --skip-deps` kullanın.
Yerel MCP Inspector ile uçtan uca denemek için `mcp dev src/server.py` kullanılabilir.

## Yol Haritası

- [ ] Otomatik test paketi (`pytest`) ve CI iş akışı
- [ ] Bütçe ve nakit akışı için dönemsel kırılım tablosu
- [ ] Excel/PPTX okuma desteği
- [ ] Çoklu şirket veri dizini seçimi
- [ ] Yedekleme/arşivleme aracı

## Katkı

Katkılar memnuniyetle karşılanır. Küçük bir adım atmak için:

1. Depoyu çatallayın (fork).
2. Bir dal açın (`git switch -c ozellik/ozellik-adi`).
3. Değişikliği yapın ve `ruff` ile `mypy` kontrollerini çalıştırın.
4. README'de davranış değiştiyse dokümantasyonu güncelleyin.
5. Bir pull request açın ve neyi neden değiştirdiğinizi kısaca yazın.

Yeni bir MCP aracı eklerken `src/server.py` içindeki docstring'i de güncellemeyi unutmayın; bu
metin istemciye araç açıklaması olarak gider.

## Lisans

MIT Lisansı ile lisanslanmıştır. Tam metin için [LICENSE](LICENSE) dosyasına bakın.

```text
Copyright (c) 2026 AI Company Manager Contributors
```
