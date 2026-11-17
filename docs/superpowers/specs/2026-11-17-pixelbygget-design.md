# Pixelbygget: design

Datum: 2026-11-17. Status: utkast inför byggplan. Underlaget (siffror, juridik, förebilder, skiss) står i `OPENSVERIGE/utredningar/rutnat-2026-11-17/RESEARCH.md`. Skissen: https://claude.ai/artifact/5B6gs12im49Dga7J3amPD6.

## 1. Avsikt

Pixelstäderna på karta.opensverige.se (Tech Embassy) byggs i dag av en person med hyrda GPU:er. Pixelbygget gör det till ett gemensamt projekt: vem som helst med ett NVIDIA-kort, eller en agent de skickar, claimar en ruta i en stad, renderar och ritar om den på sin egen maskin, laddar upp resultatet, och ser rutan lysa upp på den riktiga kartan med sitt GitHub-namn på. Sverige byggs en ruta i taget, städer först.

Varför: det är det mest konkreta sättet att låta communityn bygga något synligt tillsammans, det tar bort flaskhalsen att Gustaf renderar allt själv, och det ger kartan en berättelse som växer varje vecka.

## 2. Mål, icke-mål, anti-mål

Mål

- En ny bidragsgivare går från tom maskin till uppladdad ruta på en kväll, med bara README och egen Google-nyckel.
- En godkänd ruta syns på karta.opensverige.se inom tio minuter, utan att Gustaf kör något för hand.
- Gustafs arbete per ruta är ett klick (godkänn) eller inget alls (betrodd bidragsgivare).
- En agent klarar hela flödet utan människa utom två steg: GitHub-inloggningen och Google-nyckeln.
- Byggsidan är visuell: rutnätet över kartan, tillstånd i färg, namn på rutorna, topplista.

Icke-mål (v1)

- Worker-läge där en maskin tar nästa fria ruta i en loop. Fas 2, men API:t ska göra det trivialt.
- Kredit per ruta vid hovring på den riktiga kartan. Fas 2. v1 har kreditraden "och byggarna · N rutor" och topplistan.
- Renderare på öppna data (Lantmäteriet) i stället för Google. Fas 2, separat beslut.
- Byte av bildmodell till en Apache-licensierad (Qwen-Image-Edit-2511). Separat beslut; v1 kör samma modell som i dag.
- Byar, skog, landsbygd. Ritas inte.

Anti-mål (misslyckande även om det tekniskt fungerar)

- Att en bidragsgivare måste ha skrivrätt i ett repo, en nyckel från oss, eller tillgång till R2.
- Att något Google-härlett material (källrenders) passerar genom våra system eller sprids mellan bidragsgivare.
- Att skräp, trollning eller en trasig ruta hamnar på den skarpa kartan utan att någon tittat.
- Att gigabyte hamnar i git.
- Att sömnad, vattenmasker, DZI-skärning och uppladdning till den skarpa bucketen blir publika. De stannar hos Gustaf.

## 3. Begränsningar

- Googles villkor för Map Tiles API förbjuder maskintolkning, härlett innehåll och lagring. Varje bidragsgivare renderar med egen nyckel under eget avtal. Vi tar aldrig emot, lagrar eller vidarebefordrar en källrender. README säger detta rakt ut. Vad README får lova är Gustafs ord, inte byggarens.
- Pixelkarta-Workern och R2-bucketen `pixelkarta-eu` finns och serverar sajten. Live-formatet är fast: DZI, 512 px webp, overlap 0, 0,25 m per pixel, stadsdefinition i `karta.json` (origin, heading, xMeters, yMeters, groundHeight, width, height).
- GitHub-organisationen är på Free. Inga rulesets på privata repon. Det spelar ingen roll längre: bidragsgivare commitar inget.
- Intaget kör på Mac minin (alltid på, Tailscale) eller på PC:n med RTX-kortet. Intaget behöver ingen GPU.
- Inga medlemsuppgifter. Vi lagrar GitHub-login och GitHub-id, inget mer. Inga e-postadresser.
- Sajten ai-kartan har hash-baserad CSP. Nya anrop till rut-tjänsten och nya inline-skript måste in i policyn.
- Ingen ny beroende utan att fråga Gustaf. CLI:t i Python som dagens skript, tjänsten i JavaScript som dagens Worker.

## 4. Övervägda sätt

- **Git-only** (dagens `karta.opensverige-3d`): claim är en fil, PR är uppladdningen, en dörr i CI vaktar. Enkelt, ingen tjänst. Håller inte: tusentals rutor och tiotals gigabyte, inga live-data till byggsidan, och varje bidragsgivare behöver skrivrätt eller fork.
- **Rut-tjänsten**: en liten Cloudflare Worker med D1 för rutorna och en inkorgsbucket i R2. Låser, delar ut signerade uppladdningsadresser, serverar rutnätet, tar emot godkännanden. Repot blir mall och CLI, publikt. **Valt.** Det är några hundra rader och det är det som gör att det känns som ett system.
- **Horde-läge** (som AI Horde): workers hämtar jobb ur en kö. Inte ett alternativ utan ett tillägg ovanpå rut-tjänsten. Fas 2.

## 5. Delarna

| Del | Var | Vem får se | Vad |
| --- | --- | --- | --- |
| `opensverige/pixelbygget` | GitHub, publikt (dagens `karta.opensverige-3d` döps om och görs publikt) | alla | CLI `rutor`, renderare, omritning, tjänstens kod, README, AGENTS.md |
| Rut-tjänsten | Cloudflare Worker `pixelbygget`, D1 `pixelbygget`, R2 `pixelbygget-inkorg` (WEUR) | API:t är publikt, admin bara Gustaf | lås, uppladdning, granskning, rutnät |
| Intaget | privat repo (`opensverige/pixelkarta-worker` eller nytt `pixelbygget-intag`), kör som cron på Mac minin | Gustaf | godkänd ruta blir DZI-bitar i `pixelkarta-eu`, kantmask och kredit uppdateras |
| Byggsidan | `opensverige/ai-kartan`, sidan `/pixelbygget`, menypost, krok på kartan | alla | rutnätet över kartan, panel, topplista |

Bidragsgivaren rör bara den första raden. Hen får aldrig en nyckel, ett token eller en adress till R2 utöver en engångsadress för sin egen ruta.

## 6. Rutnätet och städerna

- En **ruta** är 1024 × 1024 px i stadens kartbild, alltså 256 × 256 m. Id `<kolumn>_<rad>`, noll-indexerat från kartbildens övre vänstra hörn. Göteborg är 130 × 118 rutor, Stockholm 144 × 70, Malmö 62 × 36. En stads bredd och höjd är alltid en multipel av 1024.
- En ruta motsvarar exakt fyra DZI-bitar (2 × 2 à 512 px) på den djupaste nivån, och en bit på varje nivå ovanför.
- Tjänstens enhet är ett **blad**, inte en stad: live-id:n är `stockholm-norr`, `goteborg-norr`, `malmo-norr`, och ett "-söder" kan komma. En stad är en grupp blad för visning och kö. CLI:t tar bladets id (`rutor claim goteborg-norr 64_59`).
- Ett **blad** definieras som i `karta.json` (Kartblad: origin, heading 0, xMeters, yMeters, groundHeight, width, height) plus en mask över vilka rutor som ingår. Masken är tätortens gräns (SCB:s tätortspolygoner, öppna data) snittad med rutnätet, och rutor utan en enda byggnad (OpenStreetMap, eller Lantmäteriets fotavtryck) tas bort. Rutor utanför masken har tillståndet `ute` och går inte att claima.
- **Kö**: städer öppnas i folkmängdsordning efter tätort. Högst två städer är öppna samtidigt. Nästa öppnas när den föregående är 90 % klar. Stockholm och Malmö är klara (ritade av Gustaf). Göteborg öppnas med sina återstående rutor. Därefter Uppsala, Linköping, Västerås, Örebro, Norrköping.
- **Öppna en stad** är ett maintainer-kommando (`intag open <stad>`): skriver stadsdefinitionen till tjänsten, lägger upp ett tomt DZI-skelett (dzi-fil, karta.json-post) och en tom kantmask i `pixelkarta-eu`, och genererar vattenmasken för staden med dagens metod.

## 7. Rut-tjänsten

**Lagring (D1)**

- `cities`: id, name, cols, rows, origin_lat, origin_lon, x_meters, y_meters, ground_height, dzi_levels, state (`queued`, `open`, `done`), queue_order.
- `cells`: city, col, row, state, owner_id, owner_login, locked_at, expires_at, uploaded_at, approved_at, live_at, bytes, sha256, comment. Primärnyckel (city, col, row). Rutor utanför masken lagras med state `ute`.
- `users`: github_id, login, first_seen, approved_count, trusted (0/1), blocked (0/1).
- `events`: tid, vem, vad, ruta. Loggen som topplistan och granskningen läser.

**Tillstånd för en ruta**: `ute` · `fri` · `last` (låst) · `vantar` (uppladdad, väntar på granskning) · `godkand` · `live` · tillbaka till `fri` med `comment` när den skickas tillbaka. Övergångar bara via tjänsten.

**Inloggning**: GitHub device flow i CLI:t mot en GitHub OAuth App "Pixelbygget" (publikt client_id, inga scopes utöver standard). CLI:t sparar GitHubs user access token lokalt (`~/.config/pixelbygget/token`, bara läsbart av användaren). Varje anrop till tjänsten skickar det som Bearer; tjänsten slår upp `GET api.github.com/user` och cachar login och id tio minuter i KV. Tjänsten har inga egna lösenord eller sessioner. Admin är det GitHub-id som står i variabeln `ADMIN_GITHUB_ID`.

**Endpoints** (prefix `/v1`)

- `GET /cities` → städer med state, klara och totala rutor, köordning. Publik, cache 60 s.
- `GET /grid/{city}` → stadsdefinitionen och alla rutor som kompakta rader `[col, row, state, login, updated]`. Publik, cache 60 s, CORS för karta.opensverige.se och localhost i dev.
- `GET /me` → login, dina lås, godkända rutor, betrodd eller inte.
- `POST /claim {city, col, row}` → 201 med `expires_at`. 409 om låst eller inte fri, 403 om staden inte är öppen eller du har för många lås, 422 om rutan är `ute`.
- `POST /release {city, col, row}` → rutan blir fri om den är din och inte uppladdad.
- `PUT /upload/{blad}/{col}_{row}` med PNG:n som kropp (max 8 MB, content-type image/png) och manifestet i huvudet `X-Manifest` (base64-JSON, se avsnitt 9b). Workern skriver objektet till `pixelbygget-inkorg` via R2-bindingen under nyckeln `{blad}/{col}_{row}.png` och manifestet som `.json` bredvid. Ingen signerad adress, ingen S3-nyckel i Workern, inget SigV4-beroende. Bara för en ruta som är din och låst. Workern kontrollerar PNG-signatur, IHDR-mått (1536 eller 1024 kvadrat), sha256 mot manifestet och pipeline-versionen mot bladets, och sätter `vantar`, eller `godkand` direkt om användaren är betrodd.
- `GET /inkorg/{blad}/{col}_{row}.png` → objektet, bara för admin (förhandsvisning i kön).
- Admin: `POST /approve`, `POST /reject {comment}`, `POST /trust {login}`, `POST /block {login}`, `POST /sheets` (öppna blad), `POST /seed {blad, cells[], login}` (märk redan ritade rutor som live), `GET /queue` (rutor som väntar), `POST /live {blad, cells[], rev}` (intaget rapporterar att rutor ligger i den skarpa bucketen och vilken revision bladet nu har).
- Cron trigger varje timme: lås äldre än 48 timmar blir fria; objekt för rutor som skickats tillbaka raderas efter 30 dagar.

**Gränser**: ny användare har högst 1 lås åt gången och 20 uppladdningar per dygn; betrodd (tio godkända rutor) har 8 lås och ingen daglig gräns. Takt 60 anrop per minut och IP via ratelimits-bindingen, som i pixelkarta-Workern. Brytare `PIXELBYGGET_STANGD=1` ger 503 på allt utom GET.

## 8. CLI:t `rutor`

Python 3.11+, i repot under `cli/`, installeras med `pip install -e .` eller körs som `python -m rutor`. Icke-interaktivt utom där det måste: inga frågor i terminalen när argumenten finns. Utgångskoder: 0 ok, 1 fel i indata, 2 nekad av tjänsten (låst, gräns, stängd), 3 miljöfel (ingen CUDA, ingen nyckel), 4 nätverk.

- `rutor login`: device flow, skriver ut koden och adressen, väntar. Första gången visas villkoren (MIT för rutan, visning på karta.opensverige.se, kredit med GitHub-namn, egen Google-nyckel under eget avtal) och kräver `--accept-terms` eller ett ja.
- `rutor cities`, `rutor grid <stad> [--free]`: läser tjänsten. JSON med `--json`.
- `rutor claim <stad> <col>_<rad>` eller `rutor claim <stad> --next` (närmaste fria ruta intill en klar, så staden växer sammanhängande).
- `rutor render <stad> <col>_<rad>`: Cesium och headless Chrome med nyckeln från `.env` (`GOOGLE_MAPS_KEY`), kameran ur stadsdefinitionen. Skriver `work/<stad>/<col>_<rad>.src.png`, 1536 × 1536 px (rutan plus 256 px marginal runt om). `work/` är gitignorad. Renderaren håller en webbläsarsession per körning, så en batch kostar ett rotanrop.
- `rutor redraw <stad> <col>_<rad> [--seed N]`: dagens `redraw_tile.py` riktad om till 1536-fönstret (fyra MultiDiffusion-fönster à 1024, steg 512), skriver `work/<stad>/<col>_<rad>.png`. Elpris-kollen (`se3_price.py`) finns kvar som råd, inte som spärr, med `--wait-for-cheap` för den som vill.
- `rutor upload <stad> <col>_<rad>`: begär adress, PUT:ar filen, anmäler klart. Bara PNG:n. Skriver ut rutans adress på byggsidan.
- `rutor release`, `rutor status`, `rutor whoami`.
- Admin, bara med admin-id: `rutor approve`, `rutor reject --comment`, `rutor queue`, `rutor trust`.
- `rutor preflight`: CUDA, bitsandbytes, Chrome, nyckel finns, tjänsten svarar.

Agent-först: `AGENTS.md` beskriver ordningen, stopp-villkoren och vad som aldrig får göras (ingen nyckel i något annat än `.env`, ingen uppladdning av `.src.png`, ingen andra ruta medan en är olåst). Alla utskrifter är stabila rader (`claimed: goteborg 64_59 expires 2026-11-19T20:10Z`).

## 9. Rendering, omritning, marginal

- Rendern är 1536 px: rutan i mitten med 256 px (64 m) marginal åt alla håll, så omritningen ser grannarnas hus och gator. Källrendern lämnar aldrig maskinen.
- Omritningen körs på hela 1536-fönstret och ger en 1536-PNG. Vad som laddas upp beror på spiken i fas 0: antingen hela 1536 (intaget blandar överlappet mot grannarna i ett band om 64 m) eller den beskurna 1024-kärnan (om skarvarna inte syns). Tjänsten och intaget ska klara båda storlekarna; standard tills spiken är klar är 1536.
- Seed är valfritt. Samma seed, modell och render ger samma ruta, vilket låter Gustaf göra stickprov genom att köra om en ruta.

## 9b. Samma stil, samma källa

Stilen är fyra fasta saker och källan är tre. Mallen låser dem, manifestet bevisar dem, tjänsten kräver dem.

- **Pipeline-version**: en sträng i repot (`PIPELINE = "2026.11-a"`) som täcker modellens Hugging Face-revision (commit-hash, inte bara namnet), LoRA:ns sha256, diffusers-commit, prompttexten, fönster 1024, steg 512, sigma-schemat, och renderarens version. `rutor redraw` räknar fram hashar på det som laddats och vägrar köra om något avviker från versionen.
- **Kamera och källa**: `rutor render` tar bara bladets id och rutans id. Origin, skala, 45 grader, markhöjd, ortografisk kamera, Cesium-version, screenSpaceError, ljus och tid på dygnet kommer ur bladets definition och repots konstanter. Bidragsgivaren kan inte ändra dem utan att manifestet avslöjar det.
- **Manifestet** (`{blad}/{col}_{row}.json`): pipeline-version, hashar, seed, kamera, Google-tilesetens datum eller version så som Cesium rapporterar den, renderns sha256 (filen själv lämnar aldrig maskinen), resultatets sha256, maskin (bara GPU-modell), tid. Tjänsten tar inte emot en ruta vars pipeline-version inte är bladets. När Gustaf byter pipeline-version för ett blad måste alla rutor på bladet ritas om, så det görs bara mellan blad.
- Rutan som PNG bär manifestets hash i en `tEXt`-chunk, så en lös fil går att spåra till sitt manifest.
- Det som kan skilja mellan två bidragsgivare är alltså bara seed och Googles datum på 3D-datan. Båda syns i manifestet och i kön.

**Inga heartbeats.** Ett lås är en rad med utgångstid. Bidragsgivarens maskin skickar inget förrän rutan är klar. Byggsidan läser `GET /grid/{blad}` en gång i minuten. Tjänstens cron går en gång i timmen. Intaget var tionde minut. Det är allt.

## 10. Granskning och förtroende

- Nya bidragsgivares rutor hamnar i `vantar`. I v1 granskar Gustaf med `rutor queue` (listar rutor som väntar, laddar ner rutan och dess grannar till en lokal mapp med `--show`, öppnar dem) och `rutor approve` eller `rutor reject --comment`. Byggsidan visar kön men har ingen admin-inloggning i v1; admin-vyn med knappar kommer i fas 2 tillsammans med inloggning i webbläsaren.
- Efter tio godkända rutor blir bidragsgivaren betrodd: uppladdningar går direkt till `godkand`. Gustaf kan när som helst ta bort förtroendet eller blockera.
- En ruta som ligger live kan tas bort med `rutor reject` på en live-ruta: intaget skriver tillbaka tomma bitar, rutan blir fri.
- Vad som granskas: att rutan är i stil, att hus och gator ligger där grannarna säger, att vattnet har kant, att inget är inklistrat. Inga automatiska bildkontroller i v1 utöver PNG, mått och storlek.

## 11. Intaget

Ett Python-skript i det privata repot, cron var tionde minut på Mac minin (eller PC:n; byggaren väljer, Mac minin är alltid på). Det har egna R2-uppgifter (S3-token med läsrätt på inkorgen och skrivrätt på `pixelkarta-eu`) och admin-token mot tjänsten.

**Förlustfri master.** Live-bitarna är lossy webp utan alfa, och oritade rutor på ett öppet blad är inte tomma utan fyllda med en blank bit (samma 532-byte-bit som i dag). Intaget får därför aldrig läsa tillbaka webp och skriva webp igen. Det håller en förlustfri master per blad: en mapp med rutor som PNG (`master/{blad}/{col}_{row}.png`, 1024 eller 1536 px) på Mac minins disk med kopia i en privat R2-bucket `pixelbygget-master`. Göteborgs redan ritade rutor seedas in i mastern från Gustafs sydda förlustfria stadsbild (skärs till rutor en gång), och märks live med `POST /seed` krediterade `gustaf`. Kreditraden "och byggarna · N rutor" räknar alla live-rutor, Gustafs inräknade.

**Cache.** Live-bitarna serveras med ett års cache. Därför får en ruta aldrig skrivas över på samma adress. Varje intagskörning som ändrar ett blad ger bladet en ny revision: bitarna skrivs under `{blad}_files.r{rev}/{nivå}/{kol}_{rad}.webp`, dzi-filen som `{blad}.r{rev}.dzi`, och `karta.json` pekar på den nya revisionen. Bitar som inte ändrats kopieras inte: pixelkarta-Workern mappar `.r{rev}` till det senaste objekt som finns för den biten (en liten indexfil per blad, `{blad}.r{rev}.index`, som säger vilken revision varje bit senast skrevs i). `karta.json` och `oar/` får cache 300 sekunder i stället för ett dygn. En ny sidladdning ser rutan inom tio minuter; en flik som redan är öppen ser den vid nästa laddning. Det är den ändring i pixelkarta-Workern som ingår i v1.

Per körning:

1. `GET /queue?state=godkand`.
2. För varje ruta: hämta PNG:n ur inkorgen, verifiera sha256 mot manifestet, lägg in den i mastern, lägg bladets vattenmask över rutans område, blanda marginalen mot grannarna i mastern om 1536-format, skär fyra 512-bitar på djupaste nivån ur mastern och skriv dem som webp, räkna om föräldrabitarna uppåt ur mastern (en förälder är nedskalningen av sina fyra barn; en oritad ruta är den blanka biten).
3. Uppdatera bladets kantmask (`oar/{blad}-kant.png`, alfa i 1/128-skala) och ö-bild (`oar/{blad}.webp`) så att gluggen täcker alla live-rutor.
4. Skriv `liv/{blad}-byggare.json` ({"col_rad": "login"}) för kredit per ruta (läses av sajten i fas 2).
5. Skriv indexfilen och dzi-filen för den nya revisionen, uppdatera `karta.json`, `POST /live` med rutorna och revisionen.

Hela bladet sys aldrig om. Dagens helblads-sömnad finns kvar för en engångskörning när ett blad är helt klart och Gustaf vill ha en slutpolering.

## 12. Byggsidan i ai-kartan

- Sida `/pixelbygget` (Astro). Huvudet är sajtens vanliga. Vänsterspalt: titel, städer (öppna nu, öppnas härnäst, klara), regeln om städer först, legend, veckans topplista. Mitten: MapLibre-kartan med ett rutnätslager för vald stad, rutorna som polygoner i tillståndsfärg, namnbrickor på låsta, klara rutor visar pixelkonsten genom sajtens befintliga glugg. Högerpanel: vald ruta, tillstånd, claim-knapp och terminalkommando, steg för steg när den är din, kö-vy för admin.
- Lager och projektion: återanvänd `src/lib/pixelkarta/geometri.ts` för att gå mellan rutans pixelbox och mark. Rutnätet hämtas från `GET /grid/{city}` och ritas som en GeoJSON-källa; uppdateras var 60:e sekund.
- Byggsidan är läsande i v1: ingen inloggning i webbläsaren. Klick på en fri ruta visar kommandot (`rutor claim goteborg-norr 64_59`) med kopieringsknapp; CLI:t gör claimen. Inloggning i webbläsaren, claim med ett klick och admin-vyn är fas 2 (GitHubs device flow saknar CORS, så det kräver en proxyväg i tjänsten).
- Menyn: ny grupp efter Bidra i `Sidhuvud.astro`: "Pixelbygget", underrad "Bygg din stad, en ruta i taget", tagg "ny" de första veckorna.
- Kroken på kartan: kortet uppe till vänster byter "Vill du se din stad i pixlar? Hör av dig." mot "Bygg din stad i pixlar →" till `/pixelbygget`. Kreditraden blir "Pixelkarta © Gustaf Baltsar Garnow och byggarna · N rutor", N från `GET /cities`, med statisk text som reserv om tjänsten inte svarar.
- CSP: `connect-src` får tjänstens värd; inline-skript hashas som i dag.
- Stil: sajtens egen (tokens i `global.css`, `.knapp`, `.kort`, `.status`, `.tagg`). Skissen är referensen.

## 13. Säkerhet och missbruk

- Tjänsten har inga hemligheter som ger åtkomst till den skarpa bucketen. Intagets S3-token och admin-token finns bara på Mac minin.
- Uppladdningsadresser är engångs, kopplade till ruta och användare, max 8 MB, image/png, 15 minuter.
- Allt som laddas upp granskas av en människa tills bidragsgivaren är betrodd, och kan tas bort med ett kommando.
- Rutnätet och köns bildförhandsvisningar lämnar aldrig ut inkorgsadresser utan signatur.
- Loggen (`events`) lagrar login, ruta, handling och tid. Inga IP-adresser i D1.
- Google-nyckeln ligger i bidragsgivarens `.env`, läses av renderaren och skickas bara till Google. CLI:t vägrar läsa `.env` om filen är världsläsbar på Unix och säger varför.
- Repot är publikt: inga hemligheter, ingen Google-härledd bild, inga renders som exempel. Exempelbilder är pixelkonst, inte källrenders.

## 14. Faser

**Fas 0, spiken** (före all byggnation av tjänsten): render en Göteborg-ruta och dess åtta grannar från mallen på en annan maskin än Gustafs, med egen Google-nyckel; rita om med 1536-fönster; sy in för hand i en kopia av pyramiden; titta på skarvarna vid normal zoom. Mät tid per ruta på kortet, och antal rotanrop per körning. Spiken avgör: 1536 eller 1024 vid uppladdning, blandning eller inte, och om renderaren går att köra från mallen utan Gustaf.

**Fas 1, v1**: rut-tjänsten, CLI:t med login/claim/render/redraw/upload, intaget, byggsidan med rutnät och panel, menyposten och kroken, README och AGENTS.md, repot omdöpt och publikt. Göteborg öppnas som första stad. Klart när acceptansen i avsnitt 15 håller.

**Fas 2**: `rutor worker` (ta nästa fria ruta i loop), kredit per ruta vid hovring på kartan, GitHub-inloggning i webbläsaren för claim med ett klick, Uppsala och kön, öppna data-renderare, modellbyte till Apache-licens.

## 15. Acceptans för v1

- En person på Windows eller Linux med NVIDIA-kort, utan kontakt med Gustaf, går från `git clone` till `rutor upload` på en kväll, bara med README och egen nyckel.
- Gustaf godkänner i CLI:t. Inom tio minuter visas rutan på karta.opensverige.se vid en ny sidladdning, kreditraden räknar upp, topplistan uppdateras.
- Två bidragsgivare som ritat grannrutor har samma pipeline-version i sina manifest, och en ruta med fel version avvisas vid uppladdning.
- Skarvar mot grannrutor syns inte vid den zoom besökare normalt ser staden i (kriteriet sätts i spiken mot en referens där 2 × 2 rutor ritats i ett svep).
- En agent (Claude Code eller motsvarande) klarar flödet från AGENTS.md med bara inloggningen och nyckeln från människan.
- Tjänsten tål att 20 personer claimar samtidigt utan dubbla lås; låsen löper ut; brytaren fungerar.
- Inget i repot, tjänsten eller inkorgen innehåller en källrender.

## 16. Lämnat till byggaren

- D1-schemats exakta kolumner, index och migrationer. Testerna för tillståndsmaskinen (enhetstester i Python för CLI:t, Vitest eller Miniflare för Workern).
- Exakt felmeddelande per utgångskod, och formen på `--json`.
- Hur renderaren paketeras (Playwright mot systemets Chrome, eller Chromium via Playwright; ingen ny beroende utan att fråga).
- Hur intaget blandar marginalen om 1536 används (linjär feather i 64 m-bandet är utgångspunkten).
- Byggsidans komponentindelning och hur rutnätslagret ritas (GeoJSON-källa med fill-lager är utgångspunkten).
- Om claim-knappen på byggsidan gör claimen i v1 eller visar kommandot.
- Mac minin eller PC:n för intaget.
- Hur `karta.opensverige-3d` städas: `claims/`, `results/`, `door.yml`, `check_push.py` och testerna försvinner; `se3_price.py` och `redraw_tile.py` blir moduler i `cli/`.
- Beroenderegeln gäller det publika repot och Workern. Det privata intaget får använda geobibliotek (shapely, pyproj, en Overpass-klient) för att räkna fram bladmasken; i v1 räknas masken en gång per blad av intaget och checkas in som fil, så tjänsten själv aldrig behöver geodata. Rutan är 256 m bred men cirka 362 m djup på marken i 45-gradersvyn, och husens höjd förskjuter bilden: masken räknas därför i bladets pixelrum via `geometri.ts`-matematiken, inte i meter på marken.
- Formen på indexfilen per blad och hur pixelkarta-Workern slår upp rätt revision för en bit.

## 17. Öppet för Gustaf

Byggargranskningen 2026-11-17 lämnade fyra frågor som bara Gustaf kan svara på. Svaren förs in här.

1. **Pipelinen på PC:n.** Renderaren (Cesium, Chrome, kamera), Vast.ai-fördelaren, vattenmask-metoden, helblads-sömnaden, DZI-skärningen, `upload_r2.py` och `make_site_files.py` finns bara på PC:n. Fas 0 och intaget bygger på att de kopieras in i det privata repot. Är `redraw_tile.py` i repot exakt det som ritade de tre live-bladen (modell, LoRA, prompt, sigma-schema), eller en variant? Pinnar renderaren Cesium-version, screenSpaceError, ljus och tid, så att en render på en annan maskin landar pixelrätt mot Gustafs?
2. **Förlustfria källor.** Finns de sydda förlustfria stadsbilderna (20 till 35 GB per blad) och vattenmaskerna kvar, och får intaget en kopia till Mac minin eller en privat bucket? Utan dem kan Göteborgs ritade rutor inte seedas förlustfritt, och blandning mot grannar får ske mot lossy webp.
3. **Förutsättningar bara Gustaf kan skapa.** En GitHub OAuth App "Pixelbygget" med device flow (under organisationen), `ADMIN_GITHUB_ID`, R2-buckets `pixelbygget-inkorg` och `pixelbygget-master`, D1-databasen, och att repot döps om till `opensverige/pixelbygget` och görs publikt. Inget ände-till-ände-test går innan dessa finns.
4. **Cloudflare-plan.** Workers Free är 100 000 anrop per dygn för hela kontot, och pixelkarta-Workern drar redan på det. Byggsidans minutpollning och D1/KV/cron har egna gränser på Free. Vilken plan har kontot? Avgör om pollningen får vara 60 s och om tjänsten behöver egen plan.
