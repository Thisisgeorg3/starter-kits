# Attack Detector Bot Release Notes

## v0.3.43 (June 2 2023: beta)
- added contextual information around losses to alerts

## v0.3.42 (June 1 2023: beta, June 2 2023: prod)
- added large profit bot to high precision bots
- relaxed logic to fire when more than one high precision bots fire
- added FP mitigation logic for Polygon validators

## v0.3.41 (May 11 2023: beta, May 31 2023: prod)
- Attack detector often reports on end user related attacks, such as rake tokens, rug pulls as these attacks often follow the same patterns as a protocol exploit of funding, preparation, exploitation, and money laundering. The attack detector is supposed to only emit protocol exploits though. In this version, a new filter has been added where EOAs that are associated with specific end user attacks are degraded to a new alert Id: ATTACK-DETECTOR-6 (only will emitted in the beta version of the bot). The end user attacks are sourced from three bots: [hard rug pull](https://explorer.forta.network/bot/0xc608f1aff80657091ad14d974ea37607f6e7513fdb8afaa148b3bff5ba305c15
), [soft rug pull](https://explorer.forta.network/bot/0x1a6da262bff20404ce35e8d4f63622dd9fbe852e5def4dc45820649428da9ea1
) and [rake token bot](https://explorer.forta.network/bot/0x36be2983e82680996e6ccc2ab39a506444ab7074677e973136fa8d914fc5dd11)


## v0.3.40 (May 10 2023: beta)
- Added [generic anomaly base bot](https://explorer.forta.network/bot/0x644b77e0d77d68d3841a55843dcdd61840ad3ca09f7e1ab2d2f5191c35f4a998).

## v0.3.39 (May 10 2023: prod, May 9 2023: beta)
- Increased redundancy of this bot. It is now deployed on 6 scan nodes as opposed to 3.
- upgraded to SDK 0.1.29. 

