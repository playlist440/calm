# Calm

**Licht dat vanzelf klopt.** Kies een ruimte, en Calm doet het licht aan als het donker is en er iemand is, geeft het de kleur die bij het moment van de dag hoort, en doet het uit zodra het daglicht het overneemt.

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

```yaml
type: custom:calm-card
entity: sensor.keuken_calm_waarom
```

Sleep de streep om te kiezen vanaf hoe donker de lampen aangaan. Het markeertje laat het daglicht van dit moment zien. Onder de balk staat wat de stand onder je vinger nu betekent, bijvoorbeeld: *Nu 34 lux daglicht op de sensor. Bij deze stand blijven de lampen uit.* Zijn de lampen aan, dan rekent de kaart hun licht eraf: dat vergelijkt Calm ook.

## Het dagverslag van een ruimte

Elke ruimte schrijft een dagverslag naar `config/calm/<ruimte>/`: één regel per meting, met de regel die besliste en de reden. Er worden twee weken bewaard. Open het in een spreadsheet als je wilt weten waarom er vorige week dinsdag om half tien iets gebeurde.

## Licentie

Apache-2.0. Zie [LICENSE](LICENSE).
