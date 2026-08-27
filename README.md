# Colophon — kućna biblioteka (specifikacija projekta)

> Self-hosted aplikacija za evidenciju **fizičkih** knjiga — šta poseduješ, šta želiš da kupiš, šta si pozajmio/prodao/poklonio — sa policama koje izgledaju kao prave police.
> **Ovo nije e-book reader.** Služi kao inventar biblioteke, casual i lep, sa vizuelnim prikazom knjiga (hrbat, korica, prave proporcije) i punim izdavačkim podacima.

- **Naziv:** Colophon *(potvrđeno — konačno)*
- **Vlasnik:** Lazar
- **Status:** faza planiranja — ništa još nije napravljeno; ovaj dokument je glavna specifikacija razvoja

---

## 1. Svrha

- Evidencija fizičkih knjiga: posedovane, željene (wishlist), pozajmljene, prodate, poklonjene
- Puni izdavački podaci (ISBN, izdavač, godina, izdanje, format, broj strana…)
- Vizuelna polica: knjige stoje jedna do druge **hrbatom napolje**; pri selekciji/pretrazi okrenute korice napred
- Knjige se crtaju u **pravoj srazmeri** prema fizičkim dimenzijama
- Mobilno responzivno; UI na engleskom
- Najvažniji prioritet dizajna: police koje liče na pravu biblioteku (drveni, cozy izgled, svetla + tamna tema)

## 2. Tehnologija i hosting

- **Backend:** FastAPI (Python)
- **Baza:** SQLite
- **Deploy:** Docker, preko Komodo stack-a
- **Domen i mašina:** Lazar bira pri deploy-u (nije deo specifikacije)
- **Bekap:** noćni SQLite bekap, retencija 30 dana; **bekap obuhvata i uploadovane fotografije**
- **Razvojni tok:** prvo radna lokalna instanca na pregled Lazaru → tek onda deploy na server

## 3. Model podataka

### Knjiga (Book)
- Naslov
- Autor(i) — više autora po knjizi
- ISBN (normalizacija ISBN-10/13, sa ili bez crtica)
- Izdavač, godina izdanja, izdanje
- Format (tvrdi povez, meki, …)
- Broj strana, jezik
- Žanr(ovi)
- Serija: naziv + broj u seriji (decimale dozvoljene za omnibuse/novele, npr. 1.5)
- Cena + gde kupljeno *(opciona polja)*
- **Kolekcionarski detalji:** obojeni rubovi (sprayed edge), potpis autora, posveta/beleška prethodnog vlasnika ili poklonodavca, slobodne beleške (notes)
- **Fotografije:** prednja korica, zadnja korica, hrbat
- **Fizičke dimenzije:** visina × širina × debljina u **mm (celi brojevi)** — bira se format ili se dimenzije unose ručno

### Primerak (Copy)
- Jedna knjiga može imati **više primeraka** (npr. paperback + hardcover, ili duplikat dobijen na poklon)
- Svaki primerak: svoj format, status, cena, kome je pozajmljen

### Životni ciklus
- Svaka knjiga ima **trajni ID** — nikada se ne briše automatski
- Brisanje postoji ali je izuzetno retko; uvek iza eksplicitne potvrde
- Pozajmica čuva **istoriju** (kome, kada, zašto, očekivani datum vraćanja)

## 4. Statusi

**Status vlasništva:** U posedu · Želim da kupim · Pozajmljeno · Prodato · Poklonjeno

**Stanje čitanja** (posebna, nezavisna osa): Nepročitano · Trenutno čitam · Pročitano · DNF

## 5. Police (shelf grupe)

1. **Moja biblioteka** — primerci u posedu (glavna polica)
2. **Pozajmljeno** — kome (ime), datum, razlog/beleška, očekivano vraćanje; istorija pozajmica se čuva
3. **Nema ih više** — prodato (cena + gde) i poklonjeno (kome + beleška); jedan shelf, filter razlikuje prodato/poklonjeno
4. **Lista želja** — knjige koje želiš da kupiš

- Pretraga ima toggle da uključi police 2 i 3 (podrazumevano isključene iz rezultata)
- Organizacija police: **po žanru**, unutar žanra sortiranje **po autoru**

## 6. Žanrovi

- **Otvoreni BISAC spisak kao izvor:** repo `edsu/bisac` — mašinski čitljiv JSON (3.737 unosa, sa kodovima i labelama), iz njega se izvlači **51 top-level kategorija** (Fiction, Fantasy, Science Fiction, Mystery, History, Biography & Autobiography, Computers, Cooking, Warhammer žanr ide pod Fiction, itd.)
- BISAC je industrijski standard (BISG); `edsu/bisac` je njegova otvorena, mašinski čitljiva verzija — koristimo je kao fiksnu listu žanrova
- OpenLibrary subjects i Google Books kategorije se **mapiraju** na te top-level kategorije kad je moguće
- Nešto što se ne uklapa: slobodan tekst sa autocomplete iz postojeće biblioteke
- Lista od 51 kategorije je fiksna u v1; po potrebi se može proširiti na podkategorije (npr. Fiction / Fantasy) u kasnijim verzijama

## 7. Serije

- Serija = naziv + broj u seriji; značka serije se prikazuje na hrbatu
- Sortiranje unutar police: **redosled u seriji > autor > naslov**
- Vrlo bitno za velike serije (Warhammer/Horus Heresy, Dan Brown, itd.)

## 8. Izgled police (dizajn)

- **Cozy drvena polica**, svetla i tamna varijanta
- **Theme-selector arhitektura** od prvog dana — nove teme se lako dodaju
- Podrazumevano: **hrbati napolje**, jedna do druge
- Selekcija / pretraga: **korice okrenute napred**
- Knjige u pravoj srazmeri prema mm dimenzijama
- **Nema fallback grid prikaza** — polica je jedini prikaz; pretraga i filteri rade unutar police

## 9. Korice i metapodaci

- Lookup po **ISBN-u ili naslovu**: OpenLibrary + Google Books, bira se najbolja korica; moguć i potpuno ručni unos svih podataka
- Upload fotografija (prednja/zadnja korica, hrbat) + **crop**; radi direktno kamerom telefona iz browsera
- **EXIF se uklanja** pri uploadu
- Ako nema slike: **generisan platneni hrbat** sa utisnutim naslovom i autorom (boja generisana, konzistentna po autoru)

## 10. Duplikati

- Pri dodavanju: upozorenje ako se poklapaju naslov + autor + ISBN
- Ponude: *„dodaj još jedan primerak"* / *„različite su"* / *„otkaži"*
- Upozorenje **nikada ne blokira** dodavanje

## 11. Pretraga i filteri

- Po: naslovu, autoru, godini, žanru, statusu, stanju čitanja (read/unread/DNF)
- **Dinamički filter po autoru** — prikazuje sve knjige tog autora preko svih žanrova

## 12. Bezbednost i pristup

- Javna površina je **samo login ekran** — nema guest prikaza, nema javne biblioteke
- bcrypt lozinka; HTTP-only session cookie (**30 dana** klizno, **hard cap 90 dana**)
- Rate-limiting + zaključavanje na login; CSRF zaštita
- Bootstrap nalog `admin` / `admin`; dok god su default kredencijali u upotrebi → **trajni banner-upozorenje** sa linkom ka Settings (ne može se trajno sakriti)
- Filtriranje podataka **na serveru**, ne samo u UI
- Nema 2FA za sada (šema ostavlja mogućnost kasnije)

## 13. Cene

- Cena je **običan broj**; u Settings se bira **simbol** koji se prikazuje (default: €)
- Bez konverzije valuta

## 14. Podešavanja

- Format datuma: dd.mm.yyyy / mm.dd.yyyy (i drugi) + vremenski format
- Jezik UI: engleski
- Simbol valute (default €)
- Tema: svetla / tamna (theme selector)

## 15. Statistika i izvoz

- **Dashboard:** broj posedovanih, na listi želja, pozajmljenih, prodatih, poklonjenih…
- **Izvoz:** CSV i JSON

## 16. Redosled razvoja

1. Model podataka (FastAPI + SQLite)
2. Shelf UI — police, hrbati, srazmere, selekcija
3. Lookup (OpenLibrary + Google Books) + upload/crop fotografija
4. Auth + podešavanja (login, rate-limit, banner za default kredencijale, teme)
5. Lokalna instanca na pregled Lazaru
6. Tek posle odobrenja: Komodo deploy (domen i mašinu bira Lazar)

---

## Otvorena pitanja

- [ ] ~~Konačna potvrda naziva~~ ✅ Colophon potvrđen
- [ ] ~~Tačna mašina za hosting~~ — bira Lazar pri deploy-u, nije u speku
- [ ] ~~Tačna lista žanrova~~ ✅ otvoreni BISAC spisak (`edsu/bisac`), 51 top-level kategorija
