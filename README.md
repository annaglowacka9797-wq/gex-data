# gex-data-collector

Codzienne, automatyczne zbieranie **profilu GEX po strike'ach** z publicznego łańcucha opcji Cboe (SPX, NDX, SPY, QQQ) i zapisywanie go w tym repozytorium. Robi to GitHub Actions, bez Twojego komputera i bez Twojego udziału. Potem Claude (skille `gex-collector` i `mq-style-options-charts`) czyta pliki z repozytorium i rysuje wykresy w stylu MenthorQ.

```
GitHub Actions (pon-pt, ok. 18:45 czasu nowojorskiego)
   -> pobiera łańcuch opcji z Cboe -> liczy GEX/DEX po strike'ach, poziomy, tabelę po terminach, skew
   -> zapisuje pliki do  data/  i commituje
Claude w rozmowie
   -> czyta data/ z raw.githubusercontent.com -> rysuje wykresy do pobrania
```

**Status: kod i testy działają na danych syntetycznych w formacie Cboe. Pobieranie z prawdziwego serwera Cboe z GitHub Actions NIE było jeszcze sprawdzone.** Pierwsze uruchomienie ręczne (krok 6) jest właśnie tym sprawdzianem.

Wszystkie liczby to wartości **modelowane z open interest** (calls +, puts −), nie obserwowane pozycje dealerów. To nie jest sygnał do handlu ani porada inwestycyjna.

## 1. Konto GitHub i anonimowość

Potrzebujesz konta GitHub. Zgodnie z regulaminem GitHuba (stan na 11.10.2026, sprawdź aktualną wersję przed założeniem konta):

- wymagany jest **prawidłowy adres e-mail**; imię i nazwisko nie są wymagane, więc **pseudonim jako nazwa użytkownika jest w porządku**;
- jedna osoba może mieć **jedno darmowe konto**; konto zakłada człowiek, nie skrypt;
- nie zakładaj kont na cudzych danych ani z fałszywym adresem e-mail: GitHub może zawiesić takie konto, a Ty stracisz dane.

Praktycznie dla prywatności:

- użyj osobnego adresu e-mail (alias lub osobna skrzynka, którą kontrolujesz i na której odbierzesz wiadomość weryfikacyjną);
- w **Settings → Emails** zaznacz *Keep my email addresses private* oraz *Block command line pushes that expose my email*;
- commity tego repozytorium robi konto `github-actions[bot]`, więc nie zawierają Twoich danych;
- repozytorium **publiczne** jest widoczne dla każdego razem z nazwą konta. Ktoś, kto zna Twój adres e-mail albo inne profile, może powiązać je z kontem, więc nie umieszczaj w repozytorium nic, co Cię identyfikuje;
- GitHub może poprosić o dodatkową weryfikację (np. CAPTCHA); nie ma „logowania anonimowego”: zawsze logujesz się na własne konto.

## 2. Czy repozytorium ma być publiczne?

Tak, jeśli Claude ma czytać pliki bez Twojego udziału: rozmowa w claude.ai pobiera je z `raw.githubusercontent.com`, co działa tylko dla repozytoriów publicznych (dla prywatnych potrzebny byłby token, którego nie chcesz wklejać do rozmowy).

Zastrzeżenie prawne, które musisz rozważyć samodzielnie: to repozytorium publikuje **dane pochodne** z darmowych, opóźnionych notowań Cboe (agregaty GEX po strike'ach, nie surowy łańcuch). Nie wiem, czy warunki korzystania z danych Cboe zezwalają na takie udostępnianie, ani czy zezwalają na automatyczne pobieranie. Przeczytaj warunki Cboe. Jeśli masz wątpliwości, wybierz wariant z Twoim komputerem (sekcja 8) albo repozytorium prywatne bez dostępu Claude'a.

## 3. Co jest w repozytorium

| Ścieżka | Opis |
|---|---|
| `src/collect.py` | główny skrypt (pobranie, walidacja, obliczenia, zapis) |
| `src/gexcore.py` | obliczenia GEX/DEX (kopia modułu ze skilla `mq-style-options-charts`) |
| `src/us_calendar.py` | kalendarz sesji NYSE (święta liczone regułą, bez internetu) |
| `src/schema.py` | walidacja plików (schemat 1) |
| `config.json` | lista symboli i parametry |
| `.github/workflows/collect.yml` | harmonogram zbierania (22:45 i 23:15 UTC, pon–pt) |
| `.github/workflows/tests.yml` | testy po Twoich zmianach w kodzie |
| `requirements.txt`, `requirements-dev.txt` | zależności (dev dodaje PyYAML do testów workflowów) |
| `tests/` | 42 testy (kalendarz, schemat, całość na danych syntetycznych, workflowy) |
| `data/` | wyniki (patrz `data/README.md`) |
| `docs/TROUBLESHOOTING.md` | komunikaty błędów i co z nimi zrobić |

## 4. Instalacja krok po kroku (tylko przeglądarka, bez programowania)

1. **Załóż konto** na github.com (sekcja 1) i potwierdź e-mail.
2. **Nowe repozytorium:** plus w prawym górnym rogu → *New repository*. Nazwa np. `gex-data`. Ustaw **Public**. Nie dodawaj README ani .gitignore (są w paczce). *Create repository*.
3. **Wgraj pliki:** na stronie nowego repozytorium kliknij *uploading an existing file* i przeciągnij **zawartość** rozpakowanego archiwum (foldery `src`, `tests`, `data`, `docs` oraz pliki `config.json`, `requirements.txt`, `README.md`, `.gitignore`). *Commit changes*.
4. **Folder `.github` często nie wgrywa się przez przeciąganie** (jest ukryty w niektórych systemach). Sprawdź w repozytorium, czy jest `.github/workflows/collect.yml`. Jeśli nie ma: *Add file → Create new file*, w polu nazwy wpisz `.github/workflows/collect.yml` (ukośniki tworzą foldery), wklej zawartość pliku `docs/workflow-copies/collect.yml` z paczki i zatwierdź. To samo zrób dla `tests.yml`.
5. **Uprawnienia:** *Settings → Actions → General → Workflow permissions* → wybierz **Read and write permissions** → *Save*. Bez tego zadanie nie zapisze danych.
6. **Pierwszy ręczny test (najważniejszy):** zakładka *Actions* → po lewej *collect* → *Run workflow*. Przy pierwszym teście w weekend zaznacz `force`, w dzień sesji po 17:00 czasu nowojorskiego zostaw odznaczone. Po 1–3 minutach przebieg ma być zielony, a w `data/` ma pojawić się folder z datą i plik `index.json`.
   - Jeśli przebieg jest czerwony: otwórz go i przeczytaj ostatnie linie logu; komunikaty opisuje `docs/TROUBLESHOOTING.md`. Najpoważniejszy możliwy powód to blokada adresów GitHuba przez Cboe (`download failed ... 403`). Wtedy przejdź do sekcji 8.
7. **Powiadomienia:** *Settings (konta) → Notifications → Actions* → włącz powiadomienia o nieudanych przebiegach. Dodatkowo każdy nieudany przebieg otwiera w repozytorium zgłoszenie (*Issue*) `Collector failed`, które zamyka się samo po udanym przebiegu.
8. **Powiedz Claude'owi, gdzie są dane:** w rozmowie podaj `nazwa-konta/gex-data`. Skill `gex-collector` zapisze nazwę w bazie dashboardu (kolekcja `config`), więc nie trzeba jej powtarzać w kolejnych rozmowach.
9. **Pilnuj harmonogramu:** w publicznych repozytoriach GitHub **wyłącza zaplanowane przebiegi po 60 dniach bez aktywności**. Dzienne commity z danymi zwykle są aktywnością, a workflow dokłada pusty commit po 30 dniach ciszy, ale nie sprawdziłem, czy GitHub liczy commity z `GITHUB_TOKEN` jako aktywność. Jeśli w zakładce *Actions* zobaczysz, że workflow jest wyłączony, kliknij *Enable workflow*. Claude przy każdym odczycie sprawdza datę najnowszych danych i ostrzega o dziurach.

## 5. Jak działa dzienny przebieg

1. Start o 22:45 UTC (18:45 w EDT, 17:45 w EST) w dni robocze; drugi slot 23:15 na wypadek opóźnienia.
2. Weekend lub święto giełdowe: skrypt kończy z komunikatem *market closed*.
3. Dla każdego symbolu: pobranie JSON z Cboe (do 4 prób), kontrola kompletności (liczba kontraktów, terminów, wiarygodność IV), kontrola daty (musi być ostatnia zakończona sesja), obliczenia, walidacja schematu.
4. Zapis plików jest atomowy i deterministyczny: ponowne uruchomienie tego samego dnia nie zmienia żadnego pliku, więc nie powstają puste commity.
5. Błąd jednego symbolu nie blokuje pozostałych. Po commicie danych przebieg kończy się na czerwono i otwiera zgłoszenie.

Rozmiar: ok. 22 KB skompresowanego pliku na symbol i dzień (4 symbole × 252 dni ≈ 22 MB rocznie).

## 6. Dodawanie symboli

W `config.json` dopisz symbol i kod Cboe (indeksy z podkreślnikiem: `_SPX`, `_NDX`, `_RUT`; ETF-y i akcje bez: `SPY`, `IWM`) oraz minimalną liczbę kontraktów, poniżej której łańcuch uznajemy za niekompletny. Zacznij od ostrożnej wartości i sprawdź pierwszy przebieg.

## 7. Testy

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Testy nie korzystają z sieci. Sprawdzają m.in.: święta NYSE 2026–2027, schemat, niezmienniki tabeli po terminach (sumy, normalizacja), zgodność poziomów z niezależnym obliczeniem, idempotencję (bajt w bajt), zmiany względem poprzedniego dnia, odrzucanie nieaktualnych i niekompletnych danych, niezależność symboli, a także poprawność workflowów (`actionlint`, brak wstrzykiwania danych wejściowych do skryptów).

## 8. Gdy Cboe blokuje GitHub Actions (plan B)

Uruchamiaj ten sam skrypt na własnym komputerze (Python 3.10+):

```
pip install -r requirements.txt
python src/collect.py --out data
```

i wgraj/skomituj folder `data/` do repozytorium (lub wgraj do rozmowy `data/latest/SPX.json`). Harmonogram lokalny: cron (Linux/macOS) albo Harmonogram zadań (Windows) na 18:45 czasu nowojorskiego.

## 9. Czego to repozytorium nie robi

- Nie zbiera cen (świece) z wiarygodnego źródła: jeśli Cboe poda `open/high/low/close`, zapisze je w `history/*_daily.csv`, ale nazwy pól to założenie niesprawdzone na żywych danych i każdy wiersz przechodzi kontrolę spójności.
- Nie przechowuje surowego łańcucha opcji (jest duży i jego ponowna publikacja to większe ryzyko prawne).
- Nie ma historii wstecz: zbiera od dnia uruchomienia.
- Open interest to wartość z poprzedniego rozliczenia, więc dzienny profil nie widzi zmian OI z tego samego dnia.
- Zmiany reguł giełdy (np. nadzwyczajne zamknięcie rynku) wykryje dopiero kontrola świeżości danych.
