# Évaluation du Control Tower Agent

- Date : 2026-10-19 20:01
- Modèle : nvidia / nvidia/nemotron-3.5-super-vl-120b-a12b (raisonnement : off)
- Questions évaluées : 31 sur 31

Notation automatique et déterministe (aucun LLM juge) : chaque question a des contrôles (chiffres attendus avec une tolérance, entités à citer, outils à appeler, livrable ou refus attendu), définis dans `eval/questions.json` à partir du corrigé caché.

## Résultat global : 30/31 questions entièrement réussies (97%), score moyen 98%

| Catégorie | Réussies | Score moyen | Objectif | Atteint |
|---|---|---|---|---|
| kpi | 7/8 | 92% | 90% | non |
| diagnostic | 9/9 | 100% | 70% | oui |
| recommandation | 7/7 | 100% | 70% | oui |
| action | 3/3 | 100% | 70% | oui |
| piege | 4/4 | 100% | 100% | oui |

## Coût et rapidité

- Temps par question : médiane 16 s, max 116 s (inclut l'espacement imposé par la limite gratuite du fournisseur)
- Appels au modèle par question : moyenne 2.8
- Tokens par question : 18,871 en entrée, 588 en sortie (moyenne)
- Total du run : 584,997 tokens en entrée, 18,239 en sortie
- Réponses au format demandé (Réponse, Chiffres clés...) : 27/27

## Détail par question

| Id | Catégorie | Résultat | Score | Temps | Outils appelés | Contrôles échoués |
|---|---|---|---|---|---|---|
| Q01 | kpi | réussi | 100% | 39 s | data_overview, kpi |  |
| Q02 | kpi | échoué | 33% | 5 s | kpi | chiffre : taux à l'heure; bons arguments : période avril-juin 2026 |
| Q03 | kpi | réussi | 100% | 7 s | kpi |  |
| Q04 | kpi | réussi | 100% | 5 s | kpi |  |
| Q05 | kpi | réussi | 100% | 6 s | kpi |  |
| Q06 | kpi | réussi | 100% | 5 s | kpi |  |
| Q07 | kpi | réussi | 100% | 8 s | data_overview, kpi |  |
| Q08 | kpi | réussi | 100% | 11 s | data_overview, kpi |  |
| Q09 | diagnostic | réussi | 100% | 70 s | analyze_premium_transport |  |
| Q10 | diagnostic | réussi | 100% | 17 s | data_overview, kpi, analyze_premium_transport, production_delay_impact |  |
| Q11 | diagnostic | réussi | 100% | 11 s | production_delay_impact |  |
| Q12 | diagnostic | réussi | 100% | 116 s | detect_carrier_degradation |  |
| Q13 | diagnostic | réussi | 100% | 22 s | lane_price_benchmark |  |
| Q14 | diagnostic | réussi | 100% | 16 s | data_overview, kpi |  |
| Q15 | diagnostic | réussi | 100% | 68 s | data_overview, kpi, lane_price_benchmark, carrier_performance, analyze_premium_transport, production_delay_impact |  |
| Q16 | diagnostic | réussi | 100% | 23 s | data_overview |  |
| Q17 | diagnostic | réussi | 100% | 11 s | compare_carbon_methods |  |
| Q18 | recommandation | réussi | 100% | 29 s | simulate_shuttle_extension |  |
| Q19 | recommandation | réussi | 100% | 9 s | find_consolidation |  |
| Q20 | recommandation | réussi | 100% | 31 s | data_overview, detect_carrier_degradation, lane_price_benchmark, carrier_performance |  |
| Q21 | recommandation | réussi | 100% | 18 s | data_overview, analyze_premium_transport, production_delay_impact, find_consolidation |  |
| Q22 | recommandation | réussi | 100% | 29 s | data_overview, analyze_premium_transport, find_consolidation, simulate_shuttle_extension, production_delay_impact, compare_carbon_methods |  |
| Q23 | recommandation | réussi | 100% | 7 s | simulate_mode_shift |  |
| Q24 | recommandation | réussi | 100% | 86 s | carrier_performance, lane_price_benchmark |  |
| Q25 | action | réussi | 100% | 14 s | detect_carrier_degradation, kpi, draft_email |  |
| Q26 | action | réussi | 100% | 78 s | data_overview, analyze_premium_transport, find_consolidation, production_delay_impact, carrier_performance, lane_price_benchmark, save_transport_plan |  |
| Q27 | action | réussi | 100% | 87 s | data_overview, lane_price_benchmark, carrier_performance, kpi, draft_email |  |
| Q28 | piege | réussi | 100% | 3 s | aucun |  |
| Q29 | piege | réussi | 100% | 2 s | aucun |  |
| Q30 | piege | réussi | 100% | 13 s | aucun |  |
| Q31 | piege | réussi | 100% | 18 s | data_overview, detect_carrier_degradation, carrier_performance, run_sql |  |

## Questions échouées : ce que l'agent a répondu

### Q02 : Quel est notre taux de livraison à l'heure sur les 3 derniers mois (avril à juin 2026) ?

- Échec « chiffre : taux à l'heure » (attendu : 83 ou 0.826)
- Échec « bons arguments : période avril-juin 2026 » (attendu : kpi({"metric": "on_time_rate", "filters": {"start": "2026-04-01", "end": "2026-06-30"}}))

> **Réponse**  
> Le taux de livraison à l'heure sur avril-juin 2026 est de 83,2 %.
> 
> **Chiffres clés**  
> - Période : 01/04/2026 au 30/06/2026  
> - Taux de livraison à l'heure : 83,2 %  
> - Nombre de livraisons analysées : 54 285  
> 
> **Méthode**  
> - Outil `kpi` avec la métrique `on_time_rate`, filtre dates du 01/04/2026 au 30/06/2026 (54 285 expéditions nettoyées).
> 
> **Limites**  
> - Aucune : la donnée est disponible et complète pour la période demandée.
> 
> **Action proposée**  
> - Aucune action immédiate ; ce KPI sert de référence pour les plans d'optimisation à venir.

