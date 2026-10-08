# Calm

**Licht dat vanzelf klopt.** De meeste opstellingen maken van een bewegingssensor een lichtschakelaar met licht als voorwaarde. Calm draait dat om: een lichtschakelaar die naar het daglicht kijkt, met beweging als voorwaarde. Kies een ruimte, en Calm doet het licht aan als het donker is en er iemand is, geeft het de kleur die bij het moment van de dag past, en doet het uit als het daglicht het overneemt.

Werkt met, en getest op, Philips Hue.

[English](README.md)

## Wat het doet

- **Ruimtes komen uit Home Assistant.** Na het installeren zie je je ruimtes in een lijst. Vink aan welke Calm regelt. Zet je later een lamp in een ruimte, dan doet hij vanzelf mee.
- **Het daglicht bepaalt wanneer.** Een lichtsensor vertelt Calm hoeveel daglicht er is. Onder de lijn die jij zet helpen de lampen mee, erboven zijn ze uit.
- **De dag bepaalt hoe.** 's Ochtends warm en zacht, midden op de dag helder en koel, richting bedtijd warmer en zachter. Gebaseerd op de wetenschappelijke consensus over licht en je biologische klok (melanopische EDI, CIE S 026 / Brown et al. 2022): eerst geeft de kleur toe, pas daarna de helderheid.
- **Je ziet het niet veranderen.** Terwijl een ruimte zich aanpast, bewegen de lampen trager dan je oog opmerkt. Binnenlopen in een donkere ruimte is de uitzondering: dan is het licht er voordat je de kamer bent overgestoken.
- **Beweging, als je wilt.** Met een bewegingssensor brandt het licht alleen als er iemand is, en gaat het naar een zachte standby of uit als de ruimte leeg is. Als je wilt geeft beweging altijd licht, ook als Calm uit staat: 's nachts een nachtlampje, overdag gewoon licht.
- **Overrules.** TV kijken, werklicht, eten, lezen en meer, elk met een eigen schakelaar. Begin vanuit een sjabloon. Een overrule kan vanzelf aangaan op vaste tijden, of als een apparaat aangaat.
- **Het legt zichzelf uit.** Elke ruimte heeft een "Waarom"-sensor die in één zin zegt waarom het licht is zoals het is.

## Wat voorgaat

Eén vaste volgorde. Wat hoger staat wint, en de "Waarom"-zin noemt altijd de regel die nu geldt.

1. **Jij met de hand.** Zet je een lamp via de Hue-app, een schakelaar of het dashboard, dan stapt Calm opzij (standaard twee uur, instelbaar).
2. **Een overrule die jij aanzette.** Die geldt, wat het daglicht ook doet.
3. **Genoeg daglicht.** De lampen gaan uit.
4. **Iemand in de ruimte.** Licht volgens het dagritme.
5. **Niemand in de ruimte.** Standby of uit.
6. **Calm regelt de ruimte niet.** Niemand thuis, nachtmodus, of je eigen automatisering zegt uit. Dan uit, behalve als beweging altijd licht geeft.

## Wat je nodig hebt

- Home Assistant 2026.9 of nieuwer.
- Lampen die in ruimtes staan.
- Een lichtsensor per ruimte. Een Hue-bewegingssensor heeft er een. Een ruimte zonder sensor kan die van een andere ruimte lenen, of een buitensensor gebruiken.

## Installeren

**HACS**: voeg deze repository toe als eigen repository (categorie: Integratie), installeer **Calm** en herstart Home Assistant.

**Met de hand**: kopieer `custom_components/calm` naar je configuratiemap en herstart.

Daarna: *Instellingen → Apparaten en diensten → Integratie toevoegen → Calm*. Vink je ruimtes aan en druk drie keer op *Volgende*.

## De Calm-kaart

Voeg hem toe via de kaartkiezer van het dashboard (zoek op *Calm*) en kies de ruimte, of in YAML:

```yaml
type: custom:calm-card
entity: sensor.keuken_calm_waarom
```

Sleep de streep om te kiezen vanaf hoe donker de lampen aangaan. Het markeertje laat het daglicht van dit moment zien. Onder de balk staat wat de stand onder je vinger nu betekent, bijvoorbeeld: *Nu 34 lux daglicht op de sensor. Bij deze stand blijven de lampen uit.* Zijn de lampen aan, dan rekent de kaart hun licht eraf, net als Calm.

## Wat elke ruimte heeft

- **Calm** (schakelaar): of Calm de ruimte nu regelt.
- **Waarom** (sensor): de reden dat de lampen zijn zoals ze zijn, uit een vaste lijst; de hele zin staat in het attribuut `sentence`.
- **Lampen aan als het donkerder is dan**, **Hoe fel overdag**, **Hoe fel 's avonds** (getallen).
- **Neem het weer over** (knop): stopt een handmatige overname meteen.
- Een schakelaar per stand, en diagnostische sensoren (daglicht, doel, kleurtemperatuur, betrouwbaarheid, lampbijdrage, verstuurde commando's), de meeste standaard uit.

Calm rekent opnieuw als een sensor iets meldt, als iemand beweegt, als een lamp verandert en elke 30 seconden. Een opdracht gaat alleen de deur uit als er genoeg veranderde.

## Ondersteunde apparaten

Alle lampen die Home Assistant kan dimmen, elke lichtsensor en elke bewegings- of aanwezigheidssensor. Dagelijks getest met Philips Hue-lampen en Hue-bewegingssensoren. Hue-ruimtes en -zones laat Calm bewust links liggen: Calm stuurt de lampen zelf aan.

## Dagverslag en diagnostiek

**Diagnostiek** (⋮ bij de integratie → *Diagnostiek downloaden*) laat zien wat elke ruimte denkt: instellingen, lampen, sensoren, de geleerde lampbijdrage en de laatste opdracht.

**Dagverslag** (standaard uit): *Calm → Configureren → Dagverslag*. Elke ruimte schrijft dan per dag een CSV naar `config/calm/<ruimte-id>/`: elke meting, wie er binnenkomt en vertrekt, en de reden achter elke beslissing. Er worden twee weken bewaard. Het legt vast wanneer ruimtes bezet zijn, en staat daarom alleen aan als je dat kiest.

## Bekende beperkingen

- **Ruimtes die dezelfde lichtsensor delen** zien elkaars lampen als daglicht. In de praktijk heeft een ruimte dan iets minder eigen licht nodig als de ruimte ernaast brandt, en dat is meestal precies wat je wilt.
- De sensor meet waar hij hangt, niet bij je ogen. Zet de streep met de kaart op het moment dat de ruimte goed voelt.
- Een Hue-bewegingssensor meldt licht ongeveer elke vijf minuten; een wolk die korter duurt, ziet hij niet.

## Problemen oplossen

- **De lampen doen niets**: kijk naar de *Waarom*-sensor van de ruimte; die noemt de regel die nu geldt. Onder Instellingen → Reparaties staat het als een voorwaarde niet meer bestaat of als lampopdrachten blijven mislukken.
- **Calm gaat onverwacht opzij**: iets anders dan Calm veranderde een lamp: een automatisering, een Hue-scène of een schakelaar. Calm logt op INFO-niveau welke lamp veranderde en wat het zelf had gevraagd.
- **De kaart zegt "Custom element doesn't exist"**: herlaad de pagina met een lege cache.

## Calm verwijderen

*Instellingen → Apparaten en diensten → Calm → ⋮ → Verwijderen*. Calm ruimt zijn opgeslagen kennis en dagverslagen mee op. De kaart haal je zelf van je dashboards.

## Ontwikkelen

De regelkern (`custom_components/calm/core`) bevat geen Home Assistant en wordt los getest, ook met hele gesimuleerde dagen. De Home Assistant-kant wordt getest tegen een echte Home Assistant.

```bash
uv venv --python 3.14 && uv pip install -r requirements_test.txt
uv run ruff check custom_components tests && uv run mypy && uv run pytest
```

## Licentie

Apache-2.0. Zie [LICENSE](LICENSE).

## Hoe Calm is ontstaan

Calm begon bij [Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting), dat liet zien dat lampen in Home Assistant de dag kunnen volgen en afblijven als iemand ze met de hand bedient. Calm deelt er geen code mee. Het stelt een andere vraag, hoeveel licht de kamer nu tekortkomt, en beantwoordt die met een lichtsensor in plaats van de stand van de zon.

---

*Alleen het idee kwam van mij; Claude is de echte engineer. Elke regel code in deze repository is geschreven door Claude (Anthropic). Ik beschreef wat ik wilde en testte het in mijn eigen huis, maar heb geen komma aan de code toegevoegd.* — playlist440
