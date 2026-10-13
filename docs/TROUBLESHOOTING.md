# Komunikaty i co z nimi zrobić

Komunikaty widać w logu przebiegu (*Actions* → kliknij przebieg → krok *Collect*) oraz w zgłoszeniu `Collector failed`.

| Komunikat | Znaczenie | Co zrobić |
|---|---|---|
| `market closed on YYYY-MM-DD (weekend/holiday): nothing to do` | Dzień bez sesji | Nic. Przy teście ręcznym w weekend zaznacz `force`. |
| `download failed after 4 tries: HTTP Error 403` (lub 429, timeout) | Cboe nie odpowiada serwerom GitHuba albo ogranicza zapytania | Spróbuj ponownie następnego dnia (drugi slot 23:15 też próbuje). Jeśli stale 403: plan B z README (uruchamianie na własnym komputerze). |
| `response too small ... not a full chain` | Odpowiedź to strona błędu, nie łańcuch | Jak wyżej. |
| `stale payload: timestamp ... but the latest finished session is ...` | Cboe zwróciło dane z wcześniejszego dnia | Zwykle opóźnienie po stronie Cboe lub nadzwyczajne zamknięcie giełdy. Poczekaj do następnego przebiegu. Nie używaj `force`, żeby „naprawić" dziurę: zapisałbyś stare dane jako nowe. |
| `intraday snapshot ... rerun after the close` | Uruchomiono przed 16:30 czasu nowojorskiego | Uruchom po zamknięciu albo zaznacz `allow_intraday` (plik dostanie ostrzeżenie `intraday snapshot`). |
| `only N usable contracts (< min): incomplete chain` | Łańcuch za krótki | Sprawdź, czy próg `min_contracts` w `config.json` nie jest za wysoki dla tego symbolu; jeśli nie, to błąd po stronie źródła: poczekaj. |
| `median IV ... outside 3%..250%: IV field suspect` | Zmienił się format IV (procenty zamiast ułamków) | Nie zapisujemy takich danych. Zgłoś zmianę formatu (Issue); wymaga poprawki w `gexcore.py`. |
| `more than half of the contracts already expired ...` | Zły znacznik czasu w danych | Poczekaj; jeśli się powtarza, format Cboe się zmienił. |
| `profile failed validation: ...` | Wynik nie przeszedł schematu | Błąd w kodzie lub nietypowe dane; nic nie zapisano. |
| `unexpected <Typ>: ...` | Nieprzewidziany wyjątek w jednym symbolu | Pozostałe symbole zostały zapisane. Skopiuj log do zgłoszenia. |
| `git push` odrzucony / `Permission denied` | Brak uprawnień zapisu | *Settings → Actions → General → Workflow permissions* → Read and write. |
| Workflow oznaczony jako disabled | GitHub wyłączył harmonogram po 60 dniach ciszy | *Actions* → workflow → *Enable workflow*. |
| Brak przebiegów o 22:45 UTC | GitHub opóźnia zadania zaplanowane, czasem o kilkanaście minut; po wyłączeniu workflow w ogóle ich nie uruchamia | Sprawdź zakładkę *Actions*; drugi slot 23:15 to zapas. |
| Brak commitów mimo zielonego przebiegu | Dzień zamknięty albo dane już zebrane (`unchanged`) | Sprawdź komunikaty `OK SPX: unchanged ...` w logu. |

## Kontrola świeżości po stronie Claude'a

Przy każdym odczycie skill sprawdza `data/index.json`: ostrzega, gdy najnowsza data jest starsza niż ostatnia zakończona sesja giełdowa albo gdy brakuje dni.
