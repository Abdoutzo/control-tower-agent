# Évaluation du Control Tower Agent

- Date : 2026-10-18 22:18
- Modèle : nvidia / nvidia/nemotron-3.5-super-vl-120b-a12b (raisonnement : on), prompt d64a45a8
- Questions évaluées : 31 sur 31

Notation automatique et déterministe (aucun LLM juge) : chaque question a des contrôles (chiffres attendus avec une tolérance, entités à citer, outils à appeler, livrable ou refus attendu), définis dans `eval/questions.json` à partir du corrigé caché.

## Résultat global : 30/31 questions entièrement réussies (97%), score moyen 98%

| Catégorie | Réussies | Score moyen | Objectif | Atteint |
|---|---|---|---|---|
| kpi | 8/8 | 100% | 90% | oui |
| diagnostic | 8/9 | 94% | 70% | oui |
| recommandation | 7/7 | 100% | 70% | oui |
| action | 3/3 | 100% | 70% | oui |
| piege | 4/4 | 100% | 100% | oui |

## Coût et rapidité

- Temps par question : médiane 20 s, max 154 s (inclut l'espacement imposé par la limite gratuite du fournisseur)
- Appels au modèle par question : moyenne 2.5
- Tokens par question : 17,182 en entrée, 1,381 en sortie (moyenne)
- Total du run : 532,657 tokens en entrée, 42,798 en sortie
- Réponses au format demandé (Réponse, Chiffres clés...) : 27/27

## Détail par question

| Id | Catégorie | Résultat | Score | Temps | Outils appelés | Contrôles échoués |
|---|---|---|---|---|---|---|
| Q01 | kpi | réussi | 100% | 38 s | kpi |  |
| Q02 | kpi | réussi | 100% | 20 s | kpi |  |
| Q03 | kpi | réussi | 100% | 14 s | kpi |  |
| Q04 | kpi | réussi | 100% | 6 s | kpi |  |
| Q05 | kpi | réussi | 100% | 7 s | kpi |  |
| Q06 | kpi | réussi | 100% | 8 s | kpi |  |
| Q07 | kpi | réussi | 100% | 20 s | data_overview, kpi |  |
| Q08 | kpi | réussi | 100% | 154 s | data_overview, kpi |  |
| Q09 | diagnostic | réussi | 100% | 47 s | analyze_premium_transport |  |
| Q10 | diagnostic | réussi | 100% | 59 s | data_overview, kpi, analyze_premium_transport, production_delay_impact |  |
| Q11 | diagnostic | réussi | 100% | 12 s | production_delay_impact |  |
| Q12 | diagnostic | réussi | 100% | 7 s | detect_carrier_degradation |  |
| Q13 | diagnostic | réussi | 100% | 74 s | lane_price_benchmark |  |
| Q14 | diagnostic | réussi | 100% | 10 s | data_overview, kpi |  |
| Q15 | diagnostic | échoué | 50% | 79 s | data_overview, kpi, carrier_performance, lane_price_benchmark, production_delay_impact | cite : part évitable; bon outil appelé; bons arguments : aérien au départ de CAS |
| Q16 | diagnostic | réussi | 100% | 68 s | data_overview |  |
| Q17 | diagnostic | réussi | 100% | 22 s | compare_carbon_methods |  |
| Q18 | recommandation | réussi | 100% | 11 s | simulate_shuttle_extension |  |
| Q19 | recommandation | réussi | 100% | 17 s | find_consolidation |  |
| Q20 | recommandation | réussi | 100% | 15 s | data_overview, detect_carrier_degradation, carrier_performance |  |
| Q21 | recommandation | réussi | 100% | 27 s | data_overview, kpi, analyze_premium_transport, production_delay_impact, find_consolidation |  |
| Q22 | recommandation | réussi | 100% | 87 s | data_overview, analyze_premium_transport, find_consolidation, kpi, simulate_shuttle_extension |  |
| Q23 | recommandation | réussi | 100% | 7 s | simulate_mode_shift |  |
| Q24 | recommandation | réussi | 100% | 46 s | data_overview, carrier_performance, detect_carrier_degradation, lane_price_benchmark |  |
| Q25 | action | réussi | 100% | 22 s | detect_carrier_degradation, draft_email |  |
| Q26 | action | réussi | 100% | 109 s | data_overview, analyze_premium_transport, production_delay_impact, find_consolidation, carrier_performance, simulate_mode_shift, save_transport_plan |  |
| Q27 | action | réussi | 100% | 28 s | data_overview, lane_price_benchmark, carrier_performance, draft_email |  |
| Q28 | piege | réussi | 100% | 5 s | aucun |  |
| Q29 | piege | réussi | 100% | 3 s | aucun |  |
| Q30 | piege | réussi | 100% | 4 s | aucun |  |
| Q31 | piege | réussi | 100% | 22 s | detect_carrier_degradation, carrier_performance, run_sql |  |

## Questions échouées : ce que l'agent a répondu

### Q15 : Pourquoi le transport aérien au départ de Casablanca coûte-t-il autant ?

- Échec « cite : part évitable » (attendu : évitable / evitable)
- Échec « bon outil appelé » (attendu : analyze_premium_transport)
- Échec « bons arguments : aérien au départ de CAS » (attendu : analyze_premium_transport({"mode": "aerien", "filters": {"origin": ["CAS"]}}))

> **Réponse**  
> L'aérien au départ de Casablanca coûte cher parce qu'il concentre un volume important (2 811 envois, 1 949 224 € sur la période), qu'il est imposé par une règle métier pour tous les flux clients vers le Maghreb (C-SEV : 319 865 €), et qu'il sert aussi à rattraper les retards de production de Casablanca (193 704 € d'aérien forcé, sur un total de 1,5 M€ de premium lié aux retards sur ce site). Deux transporteurs se partagent le trafic (OAF et MedFreight) à des tarifs proches (2,26 à 3,19 €/kg selon la lane), sans écart de prix anormal entre eux.
> 
> **Chiffres clés** (période 2025-01-01 → 2026-06-30)
> - Aérien CAS au total : 1 949 224 € pour 2 811 envois
>   - CAS → TLS : 1 147 087 € (1 520 envois)
>   - CAS → BOD : 482 272 € (665 envois)
>   - CAS → C-SEV (client) : 319 865 € (626 envois)
> - Coût au kg : OAF 3,06 €/kg (CAS-TLS), MDF 3,10 €/kg (CAS-TLS), MDF 3,19 €/kg (CAS-BOD), OAF 3,08 €/kg (CAS-BOD), OAF 2,26 €/kg (CAS-C-SEV), MDF 2,36 €/kg (CAS-C-SEV)
> - Retards production à CAS : 60,9 % des commandes en retard (10,5 jours de retard moyen), 40,3 % des séries (7,7 jours) ; CAS représente 48 % du premium transport forcé par les retards
> - Aérien forcé par retards production : 193 704 € (237 envois)
> 
> **Méthode**
> - `data_overview` : périmètre et qualité des données
> - `kpi` (cost_eur, shipments, weight_t) : coûts, volumes et poids de l'aérien depuis CAS par destination et transporteur
> - `kpi` (cost_eur par mode) : comparaison avec les autres modes 

