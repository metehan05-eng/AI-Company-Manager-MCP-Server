# company_data

Bu dizin, MCP sunucusunun tek veri kaynağıdır. Depoda **gerçek şirket verisi bulunmaz**; dosyalar
`create_new_company` aracı ile veya elle oluşturulur.

Sunucunun yönettiği dosyalar:

| Dosya | Açıklama |
|---|---|
| `company_profile.json` | Şirket adı, sektör, kuruluş yılı, departmanlar, vizyon, misyon ve metrikler. |
| `financials.json` | Bütçe, gelir/gider kategorileri, işlemler ve nakit akışı özeti. |
| `employees.csv` | Personel kayıtları; otomatik `EMP-0001` personel numarası. |
| `company_notes.json` | Politika, toplantı, strateji ve vizyon notları. |

Kendi belgelerinizi de bu klasöre ekleyebilirsiniz: `TXT`, `MD`, `JSON`, `CSV` ve `XLSX` okunup
yazılabilir, `PDF` ve `DOCX` ise salt okunur.

> `.gitignore` bu klasördeki gerçek verileri yok sayar. Şirket verilerinizi yanlışlıkla
> versiyonlamamak için ek bir işlem yapmanıza gerek yoktur.
