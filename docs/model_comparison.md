| Model | Reasoning | Prompt | Runs | Passed /31 | kpi /8 | diagnostic /9 | recommandation /7 | action /3 | piege /4 | Median time | Tokens in / out per question | Cost per question | Failed (all runs) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nvidia / nvidia/nemotron-3.5-super-vl-120b-a12b | on | d64a45a8 | 1 | 30 | 8 | 8 | 7 | 3 | 4 | 20 s | 17,182 / 1,381 | 0.0000 EUR (free endpoint) | Q15 |
| nvidia / nvidia/nemotron-3.5-super-vl-120b-a12b | off | d64a45a8 | 2 | 29.0 (28.0-30.0) | 7 | 9 | 6.5 (6.0-7.0) | 2.5 (2.0-3.0) | 4 | 15 (14-16) s | 17,354 / 548 | 0.0000 EUR (free endpoint) | Q02, Q22, Q26 |

Not compared (incomplete or no meta.json): 20261018-1510-google
