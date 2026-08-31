# sports-feed

A live wire on the sports industry worldwide: who takes the money, who carries
the cost, and who is governing any of it.

Built after "The Sports Industry" on Welcome to Your Galaxy.

**This is a feed on the industry, not on the games.** Results, fixtures,
transfers, previews, player ratings, medal tables, highlights and odds are
refused — that is nearly all sports coverage, and none of it is the subject.

## Where the subjects came from

The section is short, and its claim is specific: up to one in 42
student-athletes have been approached to fix or throw matches — roughly one per
one and a half teams, and only counting those who admitted it — with many
billions at stake. So fixing leads, and the betting money behind it follows
immediately. The rest of the wire asks the same question of the rest of the
industry.

| | |
|---|---|
| Fixing and throwing matches | The money behind it |
| Who governs the sport | Doping and testing |
| Pay, contracts and rights | Who builds and services it |
| Abuse and safeguarding | Young and student athletes |
| Injury and long-term harm | Rules on who may compete |
| Hosting and what it costs | Reputation and state money |
| Clubs, leagues and money | Who can watch it |
| Stadiums, land and public money | What it costs the ground it is played on |
| Animal racing and animal sports | Rules, enforcement and integrity bodies |
| What is set against it | |

## The animal racing subject

It is not from the sports section. It comes from the animal-industries part of
the page, which titles it "Animal Racing and Other Animal Sports" and describes
horses, greyhounds, dogs and pigeons bred and raced for sport and profit,
hundreds of thousands injured and killed, uncompetitive animals culled —
alongside rodeos, charreadas and jaripeos.

It is carried here because it is the same activity, and because a sports wire
that left it out would be making an editorial choice the page does not make. If
you would rather it sat with the other animal-industry material, it is one
subject block in `TOPICS` and lifts out cleanly.

## Weight

A decision (2), institutional material (2), a measured figure (1), a pending
decision with a date (1), a named jurisdiction (1), a primary source (1). At
three or more it is marked consequential.

## Sources

184 wires, 30 direct. The direct list is uneven for this subject in a specific
way: the rights and anti-corruption feeds cover the labour, mega-event and
governance half well, but nothing carried over covers sports integrity, doping
or club finance directly. The events block carries that half.

Add next, with URLs you have opened: Play the Game, the Sports Integrity
Initiative, Sport Resolutions, WADA, the Court of Arbitration for Sport,
FIFPRO, the World Players Association, Animal Aid's racing deaths tracker and
Grey2K.

## Running it

    python3 harvest_sports.py
    python3 harvest_sports.py --dry-run
    python3 verify_sources.py
