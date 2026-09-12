# Data

## `raw/cookie_cats.csv`

The Cookie Cats mobile game A/B test: 90,189 players, one row each.

| Column | Type | Description |
|---|---|---|
| `userid` | int | Unique player identifier. No duplicates — the unit of randomisation is the player. |
| `version` | str | Assignment: `gate_30` (control) or `gate_40` (treatment). |
| `sum_gamerounds` | int | Rounds played in the first 14 days after install. **Measured during the experiment.** |
| `retention_1` | bool | Player returned 1 day after installing. |
| `retention_7` | bool | Player returned 7 days after installing. |

### Provenance

Kaggle: [`yufengsui/mobile-games-ab-testing`](https://www.kaggle.com/datasets/yufengsui/mobile-games-ab-testing).
Originally published by Tactile Entertainment and popularised through a DataCamp project. A copy
is committed here so the analysis is reproducible without a Kaggle account; it is 2.6 MB.

### The one thing to know before analysing it

**Every column except `userid` and `version` is measured after assignment.** There is no
pre-experiment covariate, because these are new installs — there is no "before" for a player who
did not exist yet.

That single fact rules out CUPED and any covariate adjustment on this dataset, and makes
`sum_gamerounds` unusable either as a CUPED covariate or as a filter. Notebook 02 demonstrates
what happens if you ignore this: the estimate flips sign while its confidence interval gets
narrower.
