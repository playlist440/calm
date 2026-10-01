# Calm

**Light that is right on its own.** Most setups turn a motion sensor into a light switch with daylight as a condition. Calm turns it around: a light switch that watches the daylight, with motion as a condition. Pick a room, and Calm lights it when it is dark and somebody is there, gives the light the colour that suits the time of day, and puts it out when the daylight takes over.

Works with, and tested on, Philips Hue.

[Nederlands](README.nl.md)

## What it does

- **Rooms come from Home Assistant.** Install Calm and it lists your areas. Tick the ones it should look after. Add a lamp to an area later and it joins by itself.
- **Daylight decides when.** A light sensor tells Calm how much daylight there is. Below the line you set, the lamps help out; above it, they are off.
- **The day decides how.** Warm and gentle in the morning, bright and cool in the middle of the day, warmer and softer towards bedtime. Based on the circadian research consensus (melanopic EDI, CIE S 026 / Brown et al. 2022): colour gives way first, brightness after.
- **Nobody sees it change.** While a room adapts, the lamps move slower than the eye notices. Walking into a dark room is the exception: then the light is there before you have crossed it.
- **Movement, if you want it.** With a motion sensor a room lights only while somebody is there, and falls back to a soft standby or off when they leave. Optionally, movement always gives light, even when Calm is off: a nightlight at night, ordinary light during the day.
- **Overrides.** TV, work light, dinner, reading, and more, each with its own switch. Start from a template. An override can switch itself on at set times or when a device comes on.
- **It explains itself.** Every room has a "Why" sensor that says, in one sentence, why the light is the way it is.

## What wins

One fixed order. The higher rule wins, and the "Why" sentence names the rule that applies now.

1. **You, by hand.** Set a lamp from the Hue app, a switch or a dashboard and Calm steps aside (two hours by default, adjustable).
2. **An override you switched on.** It runs, whatever the daylight.
3. **Enough daylight.** The lamps go out.
4. **Somebody in the room.** Light by the rhythm of the day.
5. **Nobody in the room.** Standby or off.
6. **Calm not running the room.** Nobody home, night mode, or your own automation says off. Off, unless movement always gives light.

## Requirements

- Home Assistant 2026.9 or newer.
- Lamps in areas.
- A light (illuminance) sensor for each room. A Hue motion sensor has one. A room without one can borrow a sensor from another room, or use an outdoor one.

## Install

**HACS**: add this repository as a custom repository (category: Integration), install **Calm**, and restart Home Assistant.

**By hand**: copy `custom_components/calm` into your configuration folder and restart.

Then: *Settings → Devices & services → Add integration → Calm*. Tick your rooms and press *Next* three times.

## The Calm card

```yaml
type: custom:calm-card
entity: sensor.kitchen_calm_why
```

Drag the line to set from how dark the lamps come on. The marker shows the daylight right now. Under the bar the card says what the setting under your finger means now, for example: *Now 34 lux of daylight on the sensor. At this setting the lamps stay off.* With the lamps on it takes their light off first, as Calm does.

## A room's own record

Every room writes a day file to `config/calm/<room>/`: one line per sensor reading, with the rule that decided and the reason. Two weeks are kept. Open it in a spreadsheet when you want to know why something happened at half past nine last Tuesday.

## Development

The control core (`custom_components/calm/core`) has no Home Assistant in it and is tested on its own, including whole simulated days. The Home Assistant side is tested against a real Home Assistant.

```bash
uv venv --python 3.14 && uv pip install pytest-homeassistant-custom-component
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).

## How Calm came about

Calm started from [Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting), which showed that lights in Home Assistant can follow the day and step aside when someone takes over by hand. Calm shares no code with it. It asks a different question, how much light the room is missing right now, and answers it from a light sensor instead of the sun's position.

---

*The idea is mine; the engineering is Claude's. Every line of code in this repository was written by Claude (Anthropic). I described what I wanted and tested it in my own home, but did not add a single comma to the code.* — playlist440
